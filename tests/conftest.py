from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PORT = 5055
BASE = f"http://127.0.0.1:{PORT}"


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def mock_app_url() -> str:
    if not _listening(PORT):
        from werkzeug.serving import make_server

        from target_app.app import create_app

        server = make_server("127.0.0.1", PORT, create_app(), threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        for _ in range(50):
            if _listening(PORT):
                break
            time.sleep(0.1)
    return BASE


@pytest.fixture(scope="session")
def policy():
    from cua.policy.engine import Policy

    return Policy.load(ROOT / "policy" / "allowlist.yaml")


@pytest.fixture(scope="session")
def profile():
    from cua.agent.recorder import load_profile

    return load_profile(ROOT / "profiles" / "cu_core.yaml")


@pytest.fixture
def evidence(tmp_path, policy):
    from cua.evidence.logger import RunEvidence

    return RunEvidence(tmp_path / "evidence", "test-run", policy.redactor)


@pytest.fixture
def surface():
    from cua.surface.browser import PlaywrightSurface

    s = PlaywrightSurface(headed=False)
    s.start()
    yield s
    s.stop()


@pytest.fixture(scope="session")
def lookup_artifact(mock_app_url, policy, profile, tmp_path_factory):
    from cua.agent.loop import DiscoveryRunner
    from cua.agent.mock_llm import MockLLM
    from cua.agent.recorder import build_artifact
    from cua.evidence.logger import RunEvidence
    from cua.surface.browser import PlaywrightSurface

    ev = RunEvidence(tmp_path_factory.mktemp("ev"), "disc", policy.redactor)
    s = PlaywrightSurface(headed=False)
    s.start()
    try:
        runner = DiscoveryRunner(s, MockLLM(), policy, ev, None, max_steps=12)
        res = runner.run("Look up member {{member_id}} and read the current balance of their regular savings (S01) account.",
                         f"{mock_app_url}/teller/members/search", {"member_id": "12345"})
    finally:
        s.stop()
    assert res.success, res.stop_reason
    return build_artifact(res, name="lookup_member_savings_balance", profile=profile)
