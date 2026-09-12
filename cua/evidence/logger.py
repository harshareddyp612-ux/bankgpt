from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..policy.redact import Redactor


def new_run_id(kind: str) -> str:
    return f"{kind}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


class RunEvidence:
    def __init__(self, root: str | Path, run_id: str, redactor: Redactor | None = None, echo: bool = False):
        self.run_id = run_id
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor or Redactor()
        self.echo = echo
        self._seq = 0
        self._t0 = time.time()
        self._log_path = self.dir / "run.jsonl"

    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        self._seq += 1
        rec = {"seq": self._seq, "t": round(time.time() - self._t0, 3), "ts": datetime.now(timezone.utc).isoformat(), "event": event}
        rec.update(self.redactor.redact_obj(fields))
        with self._log_path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        if self.echo:
            brief = {k: v for k, v in rec.items() if k not in ("seq", "ts")}
            print(f"  · {json.dumps(brief, default=str)[:220]}")
        return rec

    def screenshot(self, name: str, png: bytes | None) -> str | None:
        if not png:
            return None
        path = self.dir / f"{name}.png"
        path.write_bytes(png)
        return str(path.relative_to(self.dir))

    def write_json(self, name: str, obj: Any) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(self.redactor.redact_obj(obj), indent=2, default=str) + "\n")
        return path

    def write_text(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(self.redactor.redact(text) or "")
        return path

    def finalize(self, summary: dict[str, Any]) -> Path:
        summary = dict(summary)
        summary["run_id"] = self.run_id
        summary["duration_s"] = round(time.time() - self._t0, 2)
        summary["events"] = self._seq
        return self.write_json("summary.json", summary)
