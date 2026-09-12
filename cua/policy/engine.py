from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import yaml

from ..surface.base import Action, ElementInfo
from .redact import Redactor

Mode = Literal["discovery", "replay"]


@dataclass
class PolicyDecision:
    allowed: bool
    risk: Literal["safe", "risky"] = "safe"
    requires_confirmation: bool = False
    code: str | None = None
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        state = "ALLOW" if self.allowed else "DENY"
        if self.requires_confirmation:
            state = "CONFIRM"
        return f"{state} risk={self.risk}" + (f" code={self.code}" if self.code else "") + (" :: " + "; ".join(self.reasons) if self.reasons else "")


class Policy:
    def __init__(self, cfg: dict[str, Any], source: str = "<inline>"):
        self.source = source
        self.allowed_hosts: set[str] = {h.lower() for h in cfg.get("allowed_hosts", [])}
        self.allowed_paths = [re.compile(p) for p in cfg.get("allowed_path_patterns", [".*"])]
        self.denied_paths = [re.compile(p) for p in cfg.get("denied_path_patterns", [])]
        self.allowed_actions: set[str] = set(cfg.get("allowed_actions", []))
        risky = cfg.get("risky", {}) or {}
        self.risky_names = [re.compile(p) for p in risky.get("element_name_patterns", [])]
        self.risky_urls = [re.compile(p) for p in risky.get("url_patterns", [])]
        self.discovery_mode: str = risky.get("discovery_mode", "confirm")
        self.replay_mode: str = risky.get("replay_mode", "approved_only")
        self.redactor = Redactor((cfg.get("redaction", {}) or {}).get("patterns", []))

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        p = Path(path)
        return cls(yaml.safe_load(p.read_text()) or {}, source=str(p))

    def url_allowed(self, url: str) -> tuple[bool, str]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return False, f"scheme '{parts.scheme}' not allowed"
        host = parts.netloc.lower()
        if host not in self.allowed_hosts:
            return False, f"host '{host}' not in allowlist"
        path = parts.path or "/"
        if any(rx.search(path) for rx in self.denied_paths):
            return False, f"path '{path}' is explicitly denied"
        if not any(rx.search(path) for rx in self.allowed_paths):
            return False, f"path '{path}' not in allowed path patterns"
        return True, "ok"

    def classify_risk(self, action: Action, element: ElementInfo | None, current_url: str, recorded: dict[str, Any] | None = None) -> tuple[str, str]:
        name = ""
        role = ""
        if element is not None:
            name, role = element.name or element.text, element.role
        elif recorded:
            name, role = recorded.get("name") or recorded.get("text") or "", recorded.get("role", "")
        commits = (action.type == "click" and role == "button") or (
            action.type == "press" and str(action.options.get("key", "")).lower() == "enter"
        ) or (action.type == "type" and action.options.get("press_enter"))
        if not commits:
            return "safe", "does not commit state"
        for rx in self.risky_names:
            if name and rx.search(name):
                return "risky", f"commit control '{name}' matches risky pattern"
        path = urlsplit(current_url).path
        for rx in self.risky_urls:
            if rx.search(path):
                return "risky", f"commit action on risky route '{path}'"
        return "safe", "commit control not classified as risky"

    def evaluate(
        self,
        action: Action,
        element: ElementInfo | None,
        current_url: str,
        mode: Mode,
        *,
        artifact_approved: bool = False,
        confirm_risky: bool = False,
        recorded: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        reasons: list[str] = []
        if action.type not in self.allowed_actions:
            return PolicyDecision(False, code="ACTION_NOT_ALLOWED", reasons=[f"action '{action.type}' not in allowed_actions"])

        ok, why = self.url_allowed(current_url)
        if not ok and action.type != "navigate":
            return PolicyDecision(False, code="OFF_ALLOWLIST", reasons=[f"current page is outside the allowlist: {why}"])
        if action.type == "navigate":
            target = urljoin(current_url, str(action.options.get("url", "")))
            ok, why = self.url_allowed(target)
            if not ok:
                return PolicyDecision(False, code="NAVIGATION_BLOCKED", reasons=[f"navigate to '{target}': {why}"])
            reasons.append(f"navigation target allowed: {target}")
        href = (element.attrs.get("href") if element else None) or (recorded or {}).get("href")
        if action.type == "click" and href and not href.startswith(("javascript:", "#")):
            target = urljoin(current_url, href)
            ok, why = self.url_allowed(target)
            if not ok:
                return PolicyDecision(False, code="LINK_BLOCKED", reasons=[f"link target '{target}': {why}"])

        risk, why = self.classify_risk(action, element, current_url, recorded)
        reasons.append(why)
        if risk == "safe":
            return PolicyDecision(True, "safe", reasons=reasons)

        policy_mode = self.discovery_mode if mode == "discovery" else self.replay_mode
        if policy_mode == "allow":
            reasons.append(f"risky action allowed by {mode} policy mode 'allow'")
            return PolicyDecision(True, "risky", reasons=reasons)
        if policy_mode == "block":
            return PolicyDecision(False, "risky", code="RISKY_BLOCKED", reasons=reasons + ["risky actions are blocked in this mode"])
        if policy_mode == "approved_only":
            if artifact_approved and confirm_risky:
                reasons.append("artifact is approved and caller confirmed risky steps")
                return PolicyDecision(True, "risky", reasons=reasons)
            missing = [] if artifact_approved else ["artifact not approved"]
            if not confirm_risky:
                missing.append("caller did not pass confirm_risky")
            return PolicyDecision(True, "risky", requires_confirmation=True, code="RISKY_NEEDS_CONFIRMATION", reasons=reasons + missing)
        return PolicyDecision(True, "risky", requires_confirmation=True, code="RISKY_NEEDS_CONFIRMATION", reasons=reasons + ["operator confirmation required"])

    def describe(self) -> str:
        return (
            f"allowed hosts: {sorted(self.allowed_hosts)}; allowed paths: {[p.pattern for p in self.allowed_paths]}; "
            f"denied paths: {[p.pattern for p in self.denied_paths]}; actions: {sorted(self.allowed_actions)}; "
            f"risky handling: discovery={self.discovery_mode}, replay={self.replay_mode}"
        )
