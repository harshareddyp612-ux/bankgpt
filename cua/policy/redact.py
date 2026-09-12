from __future__ import annotations

import re
from typing import Any


class Redactor:
    def __init__(self, patterns: list[dict[str, str]] | None = None):
        self._rules: list[tuple[str, re.Pattern[str], str]] = []
        for p in patterns or []:
            self._rules.append((p["name"], re.compile(p["regex"]), p.get("replace", "[REDACTED]")))
        self._values: dict[str, str] = {}

    def add_sensitive_value(self, name: str, value: str) -> None:
        if value:
            self._values[name] = value

    def redact(self, text: str | None) -> str | None:
        if text is None:
            return None
        out = text
        for name, value in sorted(self._values.items(), key=lambda kv: -len(kv[1])):
            out = out.replace(value, f"[REDACTED:{name}]")
        for _name, rx, repl in self._rules:
            out = rx.sub(repl, out)
        return out

    def redact_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: self.redact_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.redact_obj(v) for v in obj]
        return obj
