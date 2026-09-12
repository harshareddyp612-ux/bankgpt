from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from ..artifact.schema import (
    Artifact,
    Checkpoint,
    Locator,
    LocatorCandidate,
    OutcomeDetector,
    Output,
    Parameter,
    Step,
    Target,
    WaitCondition,
)
from .loop import DiscoveryResult, TraceStep

_MONEY = re.compile(r"^\(?-?\$\s?[\d,]+(\.\d{2})?\)?$|^-?[\d,]+\.\d{2}$")
_SKIP_TOOLS = {"scroll", "wait", "human"}
_DYNAMIC_TOKEN = re.compile(
    r"[$€£]\s?[\d,]+(?:\.\d+)?|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b"
    r"|\b[A-Z]{1,4}-?\d[0-9A-Z]*\b|\b[A-Z]{1,3}[0-9A-F]{6,}\b|\b\d{3,}\b|\{\{[a-zA-Z_][a-zA-Z0-9_]*\}\}"
)


def stable_text(text: str, params: dict[str, str], dynamic_values: set[str]) -> str:
    t = _templatize(text, params) or ""
    for v in sorted(dynamic_values, key=len, reverse=True):
        if v and len(v) >= 2:
            t = t.replace(v, "\x00")
    protected: list[str] = []

    def _protect(m: re.Match[str]) -> str:
        protected.append(m.group(0))
        return f"\x01{len(protected) - 1}\x01"

    t = re.sub(r"\{\{[a-zA-Z_][a-zA-Z0-9_]*\}\}", _protect, t)
    t = _DYNAMIC_TOKEN.sub("\x00", t)
    t = re.sub(r"\x01(\d+)\x01", lambda m: protected[int(m.group(1))], t)
    fragments = [re.sub(r"\s+", " ", f).strip(" .,:;-|") for f in t.split("\x00")]
    fragments = [f for f in fragments if len(f) >= 4]
    if not fragments:
        return (_templatize(text, params) or text).strip()
    return max(fragments, key=lambda f: (len(f.split()), len(f)))


def load_profile(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    return yaml.safe_load(Path(path).read_text()) or {}


def _templatize(text: str | None, params: dict[str, str]) -> str | None:
    if text is None:
        return None
    out = text
    for name, val in sorted(params.items(), key=lambda kv: -len(kv[1])):
        if val and len(val) >= 2:
            out = out.replace(val, f"{{{{{name}}}}}")
    return out


def url_pattern(url: str, params: dict[str, str]) -> str:
    path = urlsplit(url).path or "/"
    pat = re.escape(path)
    for _name, val in sorted(params.items(), key=lambda kv: -len(kv[1])):
        if val and len(val) >= 2 and re.escape(val) in pat:
            pat = pat.replace(re.escape(val), r"\d+" if val.isdigit() else r"[^/?#]+")
    pat = re.sub(r"/(?:[A-Z]{1,3}[0-9A-F]{6,}|[0-9a-f]{8,})(?=/|$)", r"/[^/]+", pat)
    return rf"^https?://[^/]+{pat}(\?.*)?$"


def _locator_for(step: TraceStep, params: dict[str, str], dynamic_values: set[str]) -> Locator:
    el = step.element
    assert el is not None
    cands: list[LocatorCandidate] = []
    for c in el.candidates:
        values = {k: v for k, v in c.value.items()}
        drop = False
        for k, v in values.items():
            if isinstance(v, str):
                if any(dv and dv in v for dv in dynamic_values):
                    drop = True
                values[k] = _templatize(v, params)
        if not drop:
            cands.append(c.model_copy(update={"value": values}))
    if not cands:
        cands = [c for c in el.candidates if c.strategy in ("xpath", "coords")]
    label = el.name or el.label or el.near_text or (el.table_cell or {}).get("header") or el.text or el.tag
    kind = {"a": "link", "select": "dropdown", "td": "cell"}.get(el.tag, el.role or el.tag)
    if el.tag == "input":
        kind = "button" if el.attrs.get("type") in ("submit", "button", "reset") else "text box"
    return Locator(
        description=f"{_templatize(label, params)} {kind}".strip(),
        frame_path=list(el.frame_path),
        candidates=cands,
        recorded={
            "tag": el.tag, "role": el.role, "name": _templatize(el.name, params), "text": _templatize(el.text[:80], params),
            "href": _templatize(el.attrs.get("href") or "", params) or None, "type": el.attrs.get("type") or None,
        },
    )


def build_artifact(
    result: DiscoveryResult,
    *,
    name: str,
    profile: dict[str, Any] | None = None,
    description: str | None = None,
    param_descriptions: dict[str, str] | None = None,
) -> Artifact:
    if not result.success:
        raise ValueError("cannot record an artifact from an unsuccessful discovery run")
    params = {k: v for k, v in result.params.items() if v}
    profile = profile or {}
    dynamic_values = {v for v in result.outputs.values() if v}
    notes: list[str] = [f"Recorded from discovery run {result.run_id} using model {result.model} in {len(result.trace)} model steps."]

    steps: list[Step] = []
    outputs: list[Output] = []
    n = 0
    for t in result.trace:
        if t.tool in _SKIP_TOOLS or not t.ok:
            if not t.ok and t.tool not in _SKIP_TOOLS:
                notes.append(f"Dropped failed/denied discovery step {t.index} ({t.tool}: {t.message}).")
            continue
        n += 1
        sid = f"s{n:02d}"
        expect: list[WaitCondition] = []
        if t.navigated and t.url_after:
            expect.append(WaitCondition(kind="url_matches", value=url_pattern(t.url_after, params), timeout_ms=8000, description=f"screen changed to {_templatize(urlsplit(t.url_after).path, params)}"))
        target = _locator_for(t, params, dynamic_values) if t.element is not None else None
        risk_reason = t.policy if t.risk == "risky" else None
        if t.tool == "click":
            steps.append(Step(id=sid, action="click", description=f"Click {target.description}", target=target, expect=expect, risk=t.risk, risk_reason=risk_reason))
        elif t.tool == "type_text":
            text = _templatize(str(t.args.get("text", "")), params) or ""
            opts = {"clear": True}
            if t.args.get("press_enter"):
                opts["press_enter"] = True
            steps.append(Step(id=sid, action="type", description=f"Type {text} into {target.description}", target=target, input=text, options=opts, expect=expect, risk=t.risk, risk_reason=risk_reason))
        elif t.tool == "select_option":
            label = _templatize(str(t.args.get("option_label", "")), params) or ""
            steps.append(Step(id=sid, action="select", description=f"Select '{label}' in {target.description}", target=target, options={"option_label": label}, expect=expect))
        elif t.tool == "press_key":
            steps.append(Step(id=sid, action="press", description=f"Press {t.args.get('key')}", options={"key": str(t.args.get("key", "Enter"))}, expect=expect, risk=t.risk, risk_reason=risk_reason))
        elif t.tool == "navigate":
            url = _templatize(str(t.args.get("url", "")), params) or ""
            steps.append(Step(id=sid, action="navigate", description=f"Go to {url}", options={"url": url}, expect=expect))
        elif t.tool == "extract":
            oname = str(t.args.get("output_name", "")).strip()
            raw = t.extracted or ""
            otype = "currency" if _MONEY.match(raw.strip()) else ("integer" if raw.strip().isdigit() else "string")
            steps.append(Step(id=sid, action="extract", description=f"Read {oname} from {target.description}", target=target, output=oname))
            outputs.append(Output(name=oname, type=otype, description=t.reason or f"Value read from {target.description}", source_step=sid, example=raw))
        else:
            n -= 1
            notes.append(f"Skipped unsupported discovery tool {t.tool} at step {t.index}.")

    inputs = [
        Parameter(
            name=k,
            type="string",
            description=(param_descriptions or {}).get(k) or f"Value for {{{{{k}}}}} in the goal",
            required=True,
            example=None if k in result.sensitive else v,
            pattern=r"^\d+$" if v.isdigit() else None,
            sensitive=k in result.sensitive,
        )
        for k, v in params.items()
    ]
    success_text = stable_text(result.success_text, params, dynamic_values)
    if success_text != (_templatize(result.success_text, params) or result.success_text):
        notes.append(f"Checkpoint text reduced to its stable fragment: {success_text!r} (model proposed {_templatize(result.success_text, params)!r}).")
    summary = _templatize(result.summary, params) or f"Goal reached: {result.goal}"
    for oname, oval in result.outputs.items():
        if oval and len(oval) >= 2:
            summary = summary.replace(oval, f"<{oname}>")
    checkpoint = Checkpoint(
        description=summary,
        conditions=[
            WaitCondition(kind="url_matches", value=url_pattern(result.final_url, params), timeout_ms=8000, description="final screen"),
            WaitCondition(kind="text_visible", value=success_text, timeout_ms=8000, description="success text visible"),
        ],
    )
    outcomes = [OutcomeDetector.model_validate(o) for o in profile.get("outcomes", [])]
    if outcomes:
        notes.append(f"Outcome detectors ({len(outcomes)}) merged from app profile '{profile.get('app_id')}'.")
    if any(s.risk == "risky" for s in steps):
        notes.append("Contains risky (state-changing) steps: unattended replay requires approval + confirm_risky.")

    parts = urlsplit(result.target_url)
    return Artifact(
        id=str(uuid.uuid4()),
        name=name,
        version="1.0.0",
        description=description or summary,
        goal=_templatize(result.goal, params) or result.goal,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_from_run=result.run_id,
        approval="draft",
        target=Target(
            app_id=profile.get("app_id", parts.netloc.replace(":", "_")),
            surface=profile.get("surface", "legacy_web"),
            base_url=f"{parts.scheme}://{parts.netloc}",
            entry_path=_templatize(parts.path or "/", params) or "/",
            vendor=profile.get("vendor"),
            app_version=str(profile.get("app_version")) if profile.get("app_version") else None,
        ),
        inputs=inputs,
        outputs=outputs,
        steps=steps,
        checkpoint=checkpoint,
        outcomes=outcomes,
        notes=notes,
    )
