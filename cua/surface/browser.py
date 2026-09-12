from __future__ import annotations

import re
import time
from typing import Any

from playwright.sync_api import Error as PWError
from playwright.sync_api import Frame, Page, TimeoutError as PWTimeout, sync_playwright

from ..artifact.schema import LocatorCandidate, WaitCondition
from .base import Action, ActionResult, ElementInfo, Observation, Resolved, Surface, SurfaceError
from .candidates import candidates_for
from .dom_js import ENUMERATE_JS, HUMAN_RECORDER_JS, TABLE_CELL_JS


class PlaywrightSurface(Surface):
    name = "playwright-chromium"

    def __init__(self, headed: bool = True, slow_mo_ms: int = 0, cdp_port: int | None = None, viewport=(1280, 900)):
        self.headed = headed
        self.slow_mo_ms = slow_mo_ms
        self.cdp_port = cdp_port
        self.viewport = viewport
        self._pw = None
        self._browser = None
        self._context = None
        self._page: Page | None = None
        self._last_status: int | None = None
        self._human_events: list[dict[str, Any]] = []
        self._recording_human = False
        self._binding_installed = False
        self._js_dialogs: list[str] = []

    def start(self) -> None:
        self._pw = sync_playwright().start()
        args = []
        if self.cdp_port:
            args.append(f"--remote-debugging-port={self.cdp_port}")
        self._browser = self._pw.chromium.launch(headless=not self.headed, slow_mo=self.slow_mo_ms, args=args)
        self._context = self._browser.new_context(viewport={"width": self.viewport[0], "height": self.viewport[1]})
        self._page = self._context.new_page()
        self._page.set_default_timeout(8000)
        self._page.on("response", self._on_response)
        self._page.on("dialog", self._on_dialog)
        self._page.on("framenavigated", self._on_framenavigated)

    def stop(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except PWError:
                pass
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._context = self._page = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise SurfaceError("surface not started")
        return self._page

    def _on_response(self, response) -> None:
        try:
            if response.request.is_navigation_request() and response.frame == self.page.main_frame:
                self._last_status = response.status
        except PWError:
            pass

    def _on_dialog(self, dialog) -> None:
        self._js_dialogs.append(f"{dialog.type}: {dialog.message}")
        try:
            dialog.dismiss()
        except PWError:
            pass

    def _on_framenavigated(self, frame: Frame) -> None:
        if self._recording_human and frame == self.page.main_frame:
            self._human_events.append({"type": "navigate", "url": frame.url, "ts": time.time()})
            try:
                frame.evaluate(HUMAN_RECORDER_JS)
            except PWError:
                pass

    def goto(self, url: str) -> ActionResult:
        try:
            resp = self.page.goto(url, wait_until="load")
            if resp is not None:
                self._last_status = resp.status
            return ActionResult(ok=True, url_after=self.page.url, navigated=True)
        except PWTimeout:
            return ActionResult(ok=False, message=f"timeout loading {url}", error_kind="timeout", url_after=self.page.url)
        except PWError as e:
            return ActionResult(ok=False, message=f"navigation error: {e}", error_kind="nav_error", url_after=self.page.url)

    def current_url(self) -> str:
        return self.page.url

    def last_http_status(self) -> int | None:
        return self._last_status

    def page_text(self) -> str:
        try:
            return re.sub(r"\s+", " ", self.page.inner_text("body")).strip()
        except PWError:
            return ""

    def screenshot(self) -> bytes:
        return self.page.screenshot(type="png", full_page=False)

    def set_cookie(self, name: str, value: str, url: str) -> None:
        assert self._context is not None
        self._context.add_cookies([{"name": name, "value": value, "url": url}])

    def _frames(self) -> list[tuple[list[str], Frame]]:
        out: list[tuple[list[str], Frame]] = [([], self.page.main_frame)]

        def walk(frame: Frame, path: list[str]):
            for child in frame.child_frames:
                cp = path + [child.name or child.url]
                out.append((cp, child))
                walk(child, cp)

        walk(self.page.main_frame, [])
        return out

    def _frame_for(self, frame_path: list[str]) -> Frame:
        frame = self.page.main_frame
        for key in frame_path:
            nxt = next((c for c in frame.child_frames if c.name == key or c.url == key), None)
            if nxt is None:
                raise SurfaceError(f"frame '{key}' not found")
            frame = nxt
        return frame

    def observe(self, screenshot: bool = True) -> Observation:
        try:
            self.page.wait_for_load_state("load", timeout=3000)
        except PWTimeout:
            pass
        elements: list[ElementInfo] = []
        overlays: list[dict[str, Any]] = []
        url, title, text, viewport = self.page.url, "", "", {"w": 0, "h": 0}
        idx = 0
        for path, frame in self._frames():
            try:
                data = frame.evaluate(ENUMERATE_JS, {"max": 220, "max_text": 6000})
            except PWError as e:
                if not path:
                    raise SurfaceError(f"could not enumerate page: {e}") from e
                continue
            if not path:
                title, text, viewport = data["title"], data["text"], data["viewport"]
            else:
                text += " " + data["text"]
            overlays.extend({**o, "frame_path": path} for o in data["overlays"])
            for raw in data["elements"]:
                el = ElementInfo(
                    index=idx,
                    kind=raw["kind"],
                    tag=raw["tag"],
                    role=raw["role"],
                    name=raw["name"],
                    text=raw["text"],
                    attrs=raw["attrs"],
                    bbox=raw["bbox"],
                    xpath=raw["xpath"],
                    frame_path=path,
                    label=raw["label"],
                    near_text=raw["near_text"],
                    table_cell=raw["table_cell"],
                    options=raw["options"],
                    in_overlay=raw["in_overlay"],
                    disabled=raw["disabled"],
                )
                el.candidates = candidates_for(el)
                elements.append(el)
                idx += 1
        for d in self._js_dialogs:
            overlays.append({"text": f"[native dialog] {d}", "xpath": "", "frame_path": []})
        self._js_dialogs.clear()
        png = self.screenshot() if screenshot else None
        return Observation(
            url=url,
            title=title,
            elements=elements,
            page_text=text,
            overlays=overlays,
            http_status=self._last_status,
            screenshot_png=png,
            viewport=viewport,
            ts=time.time(),
        )

    def handle_for_element(self, el: ElementInfo) -> Resolved:
        frame = self._frame_for(el.frame_path)
        loc = frame.locator(f"xpath={el.xpath}")
        try:
            if loc.count() == 1:
                return Resolved(strategy="observation.xpath", handle=loc)
        except PWError:
            pass
        cx, cy = el.bbox["x"] + el.bbox["w"] / 2, el.bbox["y"] + el.bbox["h"] / 2
        return Resolved(strategy="observation.coords", handle=None, point=(cx, cy))

    def try_candidate(self, candidate: LocatorCandidate, frame_path: list[str]) -> Resolved | None:
        try:
            frame = self._frame_for(frame_path)
        except SurfaceError:
            return None
        v = candidate.value
        s = candidate.strategy
        try:
            if s == "role":
                loc = frame.get_by_role(v["role"], name=v["name"], exact=True)
            elif s == "label":
                loc = frame.get_by_label(v["text"], exact=True)
            elif s == "placeholder":
                loc = frame.get_by_placeholder(v["text"], exact=True)
            elif s == "text":
                loc = frame.get_by_text(v["text"], exact=True)
                if v.get("tag"):
                    loc = loc.and_(frame.locator(v["tag"]))
            elif s == "near_text":
                tag = v.get("control", "input")
                label = v["text"].replace("'", "\\'")
                xp = (
                    f"//*[self::td or self::th or self::label or self::span or self::div or self::font]"
                    f"[normalize-space(translate(text(),':',''))='{label}' or normalize-space(.)='{label}' or normalize-space(.)='{label}:']"
                    f"/following::{tag}[1]"
                )
                loc = frame.locator(f"xpath={xp}")
            elif s == "table_cell":
                xp = frame.evaluate(TABLE_CELL_JS, v)
                if not xp:
                    return None
                loc = frame.locator(f"xpath={xp}")
            elif s == "css":
                loc = frame.locator(v["selector"])
            elif s == "xpath":
                loc = frame.locator(f"xpath={v['xpath']}")
            elif s == "coords":
                return Resolved(strategy=s, handle=None, point=(float(v["x"]), float(v["y"])), detail="coordinates fallback")
            else:
                return None
            n = loc.count()
            if n == 1:
                if not loc.first.is_visible():
                    return None
                return Resolved(strategy=s, handle=loc.first, detail=f"{s}:{_short(v)}")
            if n > 1:
                visible = [i for i in range(min(n, 10)) if loc.nth(i).is_visible()]
                if len(visible) == 1:
                    return Resolved(strategy=s, handle=loc.nth(visible[0]), detail=f"{s}:{_short(v)} (1 of {n} visible)")
            return None
        except (PWError, KeyError):
            return None

    def perform(self, action: Action, resolved: Resolved | None) -> ActionResult:
        page = self.page
        url_before = page.url
        try:
            if action.type == "navigate":
                return self.goto(action.options["url"])
            if action.type == "press":
                page.keyboard.press(action.options.get("key", "Enter"))
            elif action.type == "scroll":
                dy = -600 if action.options.get("direction") == "up" else 600
                page.mouse.wheel(0, dy)
                page.wait_for_timeout(300)
            elif action.type == "wait":
                page.wait_for_timeout(int(action.options.get("ms", 1000)))
            elif action.type in ("click", "type", "select", "extract"):
                if resolved is None:
                    return ActionResult(ok=False, message="no resolved target", error_kind="not_found")
                if action.type == "click":
                    if resolved.handle is not None:
                        resolved.handle.click(timeout=5000)
                    else:
                        page.mouse.click(*resolved.point)
                elif action.type == "type":
                    text = action.text or ""
                    if resolved.handle is not None:
                        if action.options.get("clear", True):
                            resolved.handle.fill(text, timeout=5000)
                        else:
                            resolved.handle.type(text, timeout=5000)
                    else:
                        page.mouse.click(*resolved.point)
                        page.keyboard.press("Control+A")
                        page.keyboard.type(text)
                    if action.options.get("press_enter"):
                        page.keyboard.press("Enter")
                elif action.type == "select":
                    if resolved.handle is None:
                        return ActionResult(ok=False, message="select requires an element handle", error_kind="not_found")
                    resolved.handle.select_option(label=action.options["option_label"], timeout=5000)
                elif action.type == "extract":
                    if resolved.handle is None:
                        return ActionResult(ok=False, message="extract requires an element handle", error_kind="not_found")
                    raw = resolved.handle.inner_text(timeout=5000)
                    value = re.sub(r"\s+", " ", raw).strip()
                    return ActionResult(ok=True, extracted=value, url_after=page.url)
            else:
                return ActionResult(ok=False, message=f"unsupported action {action.type}", error_kind="unsupported")
            try:
                page.wait_for_load_state("load", timeout=4000)
            except PWTimeout:
                pass
            page.wait_for_timeout(150)
            return ActionResult(ok=True, url_after=page.url, navigated=page.url != url_before)
        except PWTimeout as e:
            kind = "blocked" if "intercepts pointer events" in str(e) or "not visible" in str(e) else "timeout"
            return ActionResult(ok=False, message=_first_line(str(e)), error_kind=kind, url_after=page.url)
        except PWError as e:
            return ActionResult(ok=False, message=_first_line(str(e)), error_kind="error", url_after=page.url)

    def wait_for(self, cond: WaitCondition) -> bool:
        page = self.page
        try:
            if cond.kind == "url_matches":
                page.wait_for_url(re.compile(str(cond.value)), timeout=cond.timeout_ms)
            elif cond.kind == "text_visible":
                page.get_by_text(str(cond.value)).first.wait_for(state="visible", timeout=cond.timeout_ms)
            elif cond.kind == "text_absent":
                page.get_by_text(str(cond.value)).first.wait_for(state="hidden", timeout=cond.timeout_ms)
            elif cond.kind == "element_visible":
                spec = cond.value if isinstance(cond.value, dict) else {"selector": str(cond.value)}
                page.locator(spec["selector"]).first.wait_for(state="visible", timeout=cond.timeout_ms)
            elif cond.kind == "load_state":
                page.wait_for_load_state(str(cond.value or "load"), timeout=cond.timeout_ms)
            elif cond.kind == "sleep_ms":
                page.wait_for_timeout(int(cond.value))
            else:
                return False
            return True
        except PWTimeout:
            return False
        except PWError:
            return False

    def session_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "surface": self.name,
            "headed": self.headed,
            "url": self.page.url,
            "window_title": self.page.title(),
        }
        if self.cdp_port:
            info["cdp_endpoint"] = f"http://127.0.0.1:{self.cdp_port}"
        return info

    def begin_human_control(self) -> None:
        self._human_events = []
        self._recording_human = True
        if not self._binding_installed:
            self.page.expose_binding("__cuaHuman", lambda _src, evt: self._record_human(evt))
            self.page.add_init_script(HUMAN_RECORDER_JS.replace("() => {", "(() => {", 1) + ")()")
            self._binding_installed = True
        try:
            self.page.evaluate(HUMAN_RECORDER_JS)
        except PWError:
            pass
        try:
            self.page.bring_to_front()
        except PWError:
            pass

    def _record_human(self, evt: dict[str, Any]) -> None:
        if self._recording_human:
            evt = dict(evt)
            evt["ts"] = time.time()
            self._human_events.append(evt)

    def end_human_control(self) -> list[dict[str, Any]]:
        self._recording_human = False
        events, self._human_events = self._human_events, []
        return events


def _short(v: dict[str, Any]) -> str:
    return ", ".join(f"{k}={str(val)[:40]}" for k, val in v.items())


def _first_line(s: str) -> str:
    return s.strip().splitlines()[0][:200] if s.strip() else s
