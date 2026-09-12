from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Literal

from ..artifact.schema import ActionType, Locator, LocatorCandidate, WaitCondition


class SurfaceError(Exception):
    ...


@dataclass
class ElementInfo:
    index: int
    kind: Literal["interactive", "readable"]
    tag: str
    role: str
    name: str
    text: str
    attrs: dict[str, str]
    bbox: dict[str, Any]
    xpath: str
    frame_path: list[str] = field(default_factory=list)
    label: str = ""
    near_text: str = ""
    table_cell: dict[str, Any] | None = None
    options: list[str] | None = None
    in_overlay: bool = False
    disabled: bool = False
    candidates: list[LocatorCandidate] = field(default_factory=list)

    def summary(self) -> str:
        bits = [f"[{self.index}]"]
        if self.kind == "interactive":
            desc = self.role or self.tag
            if self.tag == "input" and self.attrs.get("type"):
                desc = f"{self.role or 'input'}/{self.attrs['type']}"
            label = self.name or self.label or self.near_text or self.attrs.get("placeholder") or ""
            bits.append(f"{desc} \"{label}\"" if label else desc)
            if self.attrs.get("name"):
                bits.append(f"name={self.attrs['name']}")
            if self.attrs.get("value") and self.tag in ("input", "textarea") and self.attrs.get("type") not in ("submit", "button", "reset"):
                bits.append(f"value=\"{self.attrs['value']}\"")
            if self.options:
                bits.append("options=" + "|".join(self.options[:8]))
            if self.disabled:
                bits.append("(disabled)")
            if self.in_overlay:
                bits.append("(inside overlay/dialog)")
        else:
            tc = self.table_cell or {}
            ctx = ""
            if tc.get("header") and tc.get("row_anchor"):
                ctx = f" (row \"{tc['row_anchor']}\", column \"{tc['header']}\")"
            elif tc.get("row_anchor"):
                ctx = f" (label \"{tc['row_anchor']}\")"
            bits.append(f"{self.role or self.tag} \"{self.text}\"{ctx}")
        return " ".join(bits)


@dataclass
class Observation:
    url: str
    title: str
    elements: list[ElementInfo]
    page_text: str
    overlays: list[dict[str, Any]]
    http_status: int | None
    screenshot_png: bytes | None
    viewport: dict[str, int]
    ts: float

    def element(self, index: int) -> ElementInfo:
        for e in self.elements:
            if e.index == index:
                return e
        raise SurfaceError(f"no element with index {index} in the current observation")

    def fingerprint(self) -> str:
        import hashlib

        h = hashlib.sha1()
        h.update(self.url.encode())
        h.update(self.page_text[:2000].encode())
        h.update(str([(e.tag, e.name, e.attrs.get("value", "")) for e in self.elements[:80]]).encode())
        return h.hexdigest()[:12]


@dataclass
class Action:
    type: ActionType
    element_index: int | None = None
    locator: Locator | None = None
    text: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def describe(self) -> str:
        tgt = f"#{self.element_index}" if self.element_index is not None else (self.locator.description if self.locator else "")
        if self.type == "type":
            return f"type into {tgt}"
        if self.type == "navigate":
            return f"navigate to {self.options.get('url')}"
        if self.type == "press":
            return f"press {self.options.get('key')}"
        if self.type == "select":
            return f"select '{self.options.get('option_label')}' in {tgt}"
        if self.type == "extract":
            return f"extract '{self.options.get('output_name')}' from {tgt}"
        return f"{self.type} {tgt}".strip()


@dataclass
class Resolved:
    strategy: str
    handle: Any | None
    point: tuple[float, float] | None = None
    detail: str = ""


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    url_after: str = ""
    navigated: bool = False
    extracted: str | None = None
    error_kind: str | None = None


class Surface(abc.ABC):

    name: str = "abstract"

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def stop(self) -> None: ...

    @abc.abstractmethod
    def goto(self, url: str) -> ActionResult: ...

    @abc.abstractmethod
    def observe(self, screenshot: bool = True) -> Observation: ...

    @abc.abstractmethod
    def try_candidate(self, candidate: LocatorCandidate, frame_path: list[str]) -> Resolved | None: ...

    @abc.abstractmethod
    def handle_for_element(self, el: ElementInfo) -> Resolved: ...

    @abc.abstractmethod
    def perform(self, action: Action, resolved: Resolved | None) -> ActionResult: ...

    @abc.abstractmethod
    def wait_for(self, cond: WaitCondition) -> bool: ...

    @abc.abstractmethod
    def current_url(self) -> str: ...

    @abc.abstractmethod
    def page_text(self) -> str: ...

    @abc.abstractmethod
    def last_http_status(self) -> int | None: ...

    @abc.abstractmethod
    def screenshot(self) -> bytes: ...

    @abc.abstractmethod
    def set_cookie(self, name: str, value: str, url: str) -> None: ...

    @abc.abstractmethod
    def session_info(self) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def begin_human_control(self) -> None: ...

    @abc.abstractmethod
    def end_human_control(self) -> list[dict[str, Any]]: ...
