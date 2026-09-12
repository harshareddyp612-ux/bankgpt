from __future__ import annotations

import json
from pathlib import Path

from .schema import Artifact


def save_artifact(artifact: Artifact, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n")
    return p


def load_artifact(path: str | Path) -> Artifact:
    data = json.loads(Path(path).read_text())
    return Artifact.model_validate(data)


def list_artifacts(directory: str | Path) -> list[Artifact]:
    out = []
    for p in sorted(Path(directory).glob("*.json")):
        try:
            out.append(load_artifact(p))
        except Exception:
            continue
    return out
