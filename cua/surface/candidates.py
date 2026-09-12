from __future__ import annotations

import re

from ..artifact.schema import LocatorCandidate
from .base import ElementInfo

_DYNAMIC = re.compile(r"[$€£]|\d{3,}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}")
_GENERATED_ID = re.compile(r"\d{3,}|ctl\d|_\d+$|^[a-f0-9]{8,}$|^:|^ember|^react")


def looks_dynamic(s: str) -> bool:
    return bool(s) and bool(_DYNAMIC.search(s))


def candidates_for(el: ElementInfo) -> list[LocatorCandidate]:
    c: list[LocatorCandidate] = []
    a = el.attrs
    control_tag = el.tag if el.tag in ("input", "select", "textarea", "button", "a") else "input"

    if el.kind == "interactive":
        if el.role and el.name and not looks_dynamic(el.name):
            c.append(
                LocatorCandidate(
                    strategy="role",
                    value={"role": el.role, "name": el.name},
                    confidence=0.9,
                    rationale="accessible role + name; survives layout and attribute changes",
                )
            )
        if el.label and not looks_dynamic(el.label):
            c.append(LocatorCandidate(strategy="label", value={"text": el.label}, confidence=0.9, rationale="explicit <label> association"))
        text_like = el.tag in ("select", "textarea") or (
            el.tag == "input" and a.get("type", "text") not in ("submit", "button", "reset", "image", "checkbox", "radio")
        )
        if el.near_text and not looks_dynamic(el.near_text) and text_like:
            c.append(
                LocatorCandidate(
                    strategy="near_text",
                    value={"text": el.near_text, "control": el.tag},
                    confidence=0.78,
                    rationale="legacy table layout: first control after the labelling cell",
                )
            )
        if a.get("placeholder"):
            c.append(LocatorCandidate(strategy="placeholder", value={"text": a["placeholder"]}, confidence=0.7, rationale="placeholder text"))
        if a.get("name"):
            c.append(
                LocatorCandidate(
                    strategy="css",
                    value={"selector": f'{el.tag}[name="{a["name"]}"]'},
                    confidence=0.72,
                    rationale="server-side form field name; stable because the backend depends on it",
                )
            )
        if el.tag == "input" and a.get("type") in ("submit", "button", "reset") and el.name:
            c.append(
                LocatorCandidate(
                    strategy="css",
                    value={"selector": f'input[type="{a["type"]}"][value="{el.name}"]'},
                    confidence=0.66,
                    rationale="button value attribute",
                )
            )
        if el.tag == "a" and a.get("href") and not a["href"].startswith(("javascript:", "#")):
            c.append(
                LocatorCandidate(
                    strategy="css",
                    value={"selector": f'a[href="{a["href"]}"]'},
                    confidence=0.6 if not looks_dynamic(a["href"]) else 0.45,
                    rationale="link target; parameter values inside are templated by the recorder",
                )
            )
        if a.get("id"):
            generated = bool(_GENERATED_ID.search(a["id"]))
            c.append(
                LocatorCandidate(
                    strategy="css",
                    value={"selector": f'#{a["id"]}'},
                    confidence=0.4 if generated else 0.8,
                    rationale="element id" + (" (looks machine-generated: low confidence)" if generated else ""),
                )
            )
        if el.tag in ("a", "button") and el.text and len(el.text) <= 60 and not looks_dynamic(el.text):
            c.append(LocatorCandidate(strategy="text", value={"text": el.text, "tag": el.tag}, confidence=0.6, rationale="exact visible text"))
    else:
        tc = el.table_cell or {}
        if tc.get("header") and tc.get("row_anchor") and not looks_dynamic(tc["row_anchor"]):
            c.append(
                LocatorCandidate(
                    strategy="table_cell",
                    value={"header": tc["header"], "row_anchor": tc["row_anchor"], "col_index": tc["col_index"]},
                    confidence=0.86,
                    rationale="cell addressed by column header and row anchor; independent of the cell's value",
                )
            )
        elif tc.get("row_anchor") and not looks_dynamic(tc["row_anchor"]):
            c.append(
                LocatorCandidate(
                    strategy="table_cell",
                    value={"header": None, "row_anchor": tc["row_anchor"], "col_index": tc["col_index"]},
                    confidence=0.8,
                    rationale="key/value table: value cell addressed by its label cell",
                )
            )
        if el.text and len(el.text) <= 60 and not looks_dynamic(el.text):
            c.append(LocatorCandidate(strategy="text", value={"text": el.text, "tag": el.tag}, confidence=0.5, rationale="exact visible text"))

    c.append(LocatorCandidate(strategy="xpath", value={"xpath": el.xpath}, confidence=0.3, rationale="structural fallback; breaks on layout change"))
    c.append(
        LocatorCandidate(
            strategy="coords",
            value={"x": round(el.bbox["x"] + el.bbox["w"] / 2, 1), "y": round(el.bbox["y"] + el.bbox["h"] / 2, 1)},
            confidence=0.1,
            rationale="last resort; only meaningful for screenshot-driven surfaces at the recorded viewport",
        )
    )
    return sorted(c, key=lambda x: -x.confidence)
