from __future__ import annotations

from typing import Callable

from ..surface.base import Surface
from .handoff import InterventionRequest, ScriptedOperatorConsole

Script = Callable[[InterventionRequest, Surface], str]


def _reauth(request: InterventionRequest, surface: Surface) -> str:
    page = getattr(surface, "page", None)
    if page is None:
        return "abort"
    if "Session Expired" not in page.inner_text("body"):
        return "resume"
    page.locator('input[name="op_user"]').fill("teller1")
    page.locator('input[name="op_pass"]').fill("demo-pass")
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_load_state("load")
    return "resume"


def _approve(request: InterventionRequest, surface: Surface) -> str:
    return "resume"


def _deny(request: InterventionRequest, surface: Surface) -> str:
    return "abort"


def _dismiss_overlay(request: InterventionRequest, surface: Surface) -> str:
    page = getattr(surface, "page", None)
    if page is None:
        return "abort"
    for name in ("Dismiss", "OK", "Close", "Continue", "Acknowledge"):
        btn = page.get_by_role("button", name=name)
        if btn.count():
            btn.first.click()
            return "resume"
    return "abort"


REGISTRY: dict[str, Script] = {
    "reauth": _reauth,
    "approve": _approve,
    "deny": _deny,
    "dismiss_overlay": _dismiss_overlay,
}


def scripted_console(name: str) -> ScriptedOperatorConsole:
    if name not in REGISTRY:
        raise KeyError(f"unknown scripted operator '{name}'; available: {sorted(REGISTRY)}")
    return ScriptedOperatorConsole(name, REGISTRY[name])
