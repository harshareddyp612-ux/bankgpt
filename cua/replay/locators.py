from __future__ import annotations

import time
from typing import Any

from ..artifact.schema import Artifact, Locator, LocatorCandidate
from ..surface.base import Resolved, Surface


def render_candidate(cand: LocatorCandidate, params: dict[str, str]) -> LocatorCandidate:
    rendered: dict[str, Any] = {}
    for k, v in cand.value.items():
        rendered[k] = Artifact.render(v, params) if isinstance(v, str) else v
    return cand.model_copy(update={"value": rendered})


def resolve_locator(
    surface: Surface,
    locator: Locator,
    params: dict[str, str],
    *,
    allow_coords: bool = False,
    passes: int = 2,
    pause_s: float = 0.7,
) -> tuple[Resolved | None, list[dict[str, Any]]]:
    tried: list[dict[str, Any]] = []
    for attempt in range(passes):
        for cand in locator.candidates:
            if cand.strategy == "coords" and not allow_coords:
                tried.append({"strategy": "coords", "skipped": "coords disabled"})
                continue
            rc = render_candidate(cand, params)
            res = surface.try_candidate(rc, locator.frame_path)
            tried.append({"strategy": cand.strategy, "confidence": cand.confidence, "ok": res is not None, "pass": attempt + 1})
            if res is not None:
                return res, tried
        if attempt + 1 < passes:
            time.sleep(pause_s)
    return None, tried
