from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..artifact.schema import OutcomeDetector, Recovery

GENERIC_OUTCOMES: list[OutcomeDetector] = [
    OutcomeDetector(
        code="SERVER_ERROR",
        kind="recoverable",
        description="Application returned HTTP 5xx; reload once, then treat as a hard failure",
        detect={"http_status": ">=500"},
        recovery=Recovery(action="reload", max_attempts=1, delay_ms=1500, description="reload the page once"),
    ),
    OutcomeDetector(
        code="PERMISSION_DENIED",
        kind="hard",
        description="HTTP 403 - the operator role cannot perform this action; a supervisor may be able to",
        detect={"http_status": 403},
        escalate=True,
    ),
    OutcomeDetector(
        code="PERMISSION_DENIED",
        kind="hard",
        description="Access denied text on page",
        detect={"text_regex": r"(?i)\baccess denied\b|\bnot authori[sz]ed\b|\bpermission denied\b"},
        escalate=True,
    ),
    OutcomeDetector(
        code="PAGE_NOT_FOUND",
        kind="hard",
        description="HTTP 404 - the screen does not exist (route drift or bad navigation)",
        detect={"http_status": 404},
    ),
    OutcomeDetector(
        code="SESSION_EXPIRED",
        kind="hard",
        description="Session expired; re-authentication needs a human's credentials",
        detect={"text_regex": r"(?i)session (has )?expired|please (log|sign) in again"},
        escalate=True,
    ),
    OutcomeDetector(
        code="UNEXPECTED_OVERLAY",
        kind="recoverable",
        description="A large modal/overlay not described by the artifact; try a dismiss-style button, else escalate",
        detect={"overlay_text_regex": r".+"},
        recovery=Recovery(action="click", target=None, max_attempts=2, description="click a Dismiss/OK/Close/Continue button inside the overlay"),
        escalate=True,
    ),
]


@dataclass
class DetectionContext:
    url: str
    text: str
    http_status: int | None
    overlays: list[dict[str, Any]]
    step_id: str | None


@dataclass
class Detection:
    outcome: OutcomeDetector
    matched: str
    source: str


def _status_matches(spec: Any, status: int | None) -> bool:
    if status is None:
        return False
    if isinstance(spec, int):
        return status == spec
    s = str(spec).strip()
    if s.startswith(">="):
        return status >= int(s[2:])
    if s.startswith("<"):
        return status < int(s[1:])
    if s.isdigit():
        return status == int(s)
    return False


def _snippet(text: str, m: re.Match[str], width: int = 90) -> str:
    start = max(0, m.start() - 20)
    if start > 0:
        start = text.rfind(" ", 0, m.start()) + 1 if text.rfind(" ", start, m.start()) >= 0 else start
    end = min(len(text), m.end() + width)
    if end < len(text):
        sp = text.rfind(" ", m.end(), end)
        end = sp if sp > m.end() else end
    return text[start:end].strip()


def match_one(det: OutcomeDetector, ctx: DetectionContext) -> str | None:
    if det.applies_to_steps is not None and ctx.step_id not in det.applies_to_steps:
        return None
    snippet = ""
    d = det.detect
    if "http_status" in d:
        if not _status_matches(d["http_status"], ctx.http_status):
            return None
        snippet = f"HTTP {ctx.http_status}"
    if "url_regex" in d:
        if not re.search(d["url_regex"], ctx.url):
            return None
        snippet = snippet or ctx.url
    if "text_regex" in d:
        m = re.search(d["text_regex"], ctx.text)
        if not m:
            return None
        snippet = _snippet(ctx.text, m)
    if "overlay_text_regex" in d:
        hit = next((o for o in ctx.overlays if re.search(d["overlay_text_regex"], o.get("text", ""))), None)
        if hit is None:
            return None
        snippet = snippet or f"overlay: {hit.get('text', '')[:120]}"
    return snippet or "matched"


def detect(artifact_outcomes: list[OutcomeDetector], ctx: DetectionContext) -> Detection | None:
    order = {"business": 0, "recoverable": 1, "hard": 2}
    for det in sorted(artifact_outcomes, key=lambda o: order[o.kind]):
        snip = match_one(det, ctx)
        if snip is not None:
            return Detection(det, snip, "artifact")
    for det in GENERIC_OUTCOMES:
        snip = match_one(det, ctx)
        if snip is not None:
            return Detection(det, snip, "generic")
    return None
