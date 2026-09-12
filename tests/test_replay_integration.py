import pytest

from cua.escalation.control import ControlChannel
from cua.escalation.handoff import EscalationManager, NoOperatorConsole
from cua.escalation.scripted import scripted_console
from cua.replay.engine import ReplayEngine
from cua.replay.result import ReplayStatus

pytestmark = pytest.mark.integration


def _engine(surface, policy, evidence, console=None, **kw):
    esc = EscalationManager(ControlChannel(), console or NoOperatorConsole(), evidence, surface)
    return ReplayEngine(surface, policy, evidence, esc, **kw)


def test_replay_success_uses_semantic_locators(lookup_artifact, surface, policy, evidence):
    r = _engine(surface, policy, evidence).run(lookup_artifact, {"member_id": "12345"})
    assert r.status == ReplayStatus.SUCCESS, r.message
    assert r.outputs == {"savings_balance": 4512.78}
    assert [s.locator_used.split(":")[0] for s in r.steps] == ["near_text", "role", "table_cell"]
    assert r.llm_calls == 0


def test_replay_business_outcome_not_found(lookup_artifact, surface, policy, evidence):
    r = _engine(surface, policy, evidence).run(lookup_artifact, {"member_id": "99999"})
    assert r.status == ReplayStatus.BUSINESS_OUTCOME and r.outcome_code == "MEMBER_NOT_FOUND"
    assert "99999" in r.message and r.failed_step == "s02"


def test_replay_invalid_params(lookup_artifact, surface, policy, evidence):
    r = _engine(surface, policy, evidence).run(lookup_artifact, {"member_id": "abc"})
    assert r.status == ReplayStatus.INVALID_PARAMS


def test_replay_recovers_from_known_dialog(lookup_artifact, surface, policy, evidence, mock_app_url):
    surface.set_cookie("chaos", "dialog", mock_app_url)
    r = _engine(surface, policy, evidence).run(lookup_artifact, {"member_id": "12345"})
    assert r.status == ReplayStatus.SUCCESS
    s02 = next(s for s in r.steps if s.step_id == "s02")
    assert s02.status == "recovered" and s02.recoveries[0]["code"] == "SYSTEM_NOTICE_DIALOG"


def test_replay_recovers_from_server_error_by_reload(lookup_artifact, surface, policy, evidence, mock_app_url):
    surface.set_cookie("chaos", "error500", mock_app_url)
    r = _engine(surface, policy, evidence).run(lookup_artifact, {"member_id": "12345"})
    assert r.status == ReplayStatus.SUCCESS
    assert any(rec["code"] == "APP_ERROR" and rec["recovery"] == "reload" for s in r.steps for rec in s.recoveries)


def test_replay_session_expired_escalates_and_resumes(lookup_artifact, surface, policy, evidence, mock_app_url):
    surface.set_cookie("chaos", "timeout", mock_app_url)
    r = _engine(surface, policy, evidence, console=scripted_console("reauth")).run(lookup_artifact, {"member_id": "12345"})
    assert r.status == ReplayStatus.SUCCESS
    assert len(r.human_interventions) == 1
    hi = r.human_interventions[0]
    assert hi.reason_code == "SESSION_EXPIRED" and hi.decision == "resume" and hi.operator == "scripted:reauth"
    assert any(a.get("type") in ("change", "submit", "click", "navigate") for a in hi.human_actions)
    assert "demo-pass" not in (evidence.dir / "run.jsonl").read_text()


def test_replay_access_denied_unattended_aborts_with_request(lookup_artifact, surface, policy, evidence, mock_app_url):
    surface.set_cookie("chaos", "denied", mock_app_url)
    r = _engine(surface, policy, evidence).run(lookup_artifact, {"member_id": "12345"})
    assert r.status == ReplayStatus.ESCALATED_ABORTED and r.outcome_code == "ACCESS_DENIED"
    assert "unattended" in r.message
    log = (evidence.dir / "run.jsonl").read_text()
    assert '"event": "intervention.requested"' in log


def test_replay_slow_load_waits(lookup_artifact, surface, policy, evidence, mock_app_url):
    surface.set_cookie("chaos", "slow", mock_app_url)
    r = _engine(surface, policy, evidence).run(lookup_artifact, {"member_id": "12345"})
    assert r.status == ReplayStatus.SUCCESS


def test_locator_fallback_when_primary_strategy_breaks(lookup_artifact, surface, policy, evidence):
    art = lookup_artifact.model_copy(deep=True)
    s02 = next(s for s in art.steps if s.id == "s02")
    s02.target.candidates = [c for c in s02.target.candidates if c.strategy in ("xpath", "coords")]
    r = _engine(surface, policy, evidence).run(art, {"member_id": "12345"})
    assert r.status == ReplayStatus.SUCCESS
    assert next(s for s in r.steps if s.step_id == "s02").locator_used.startswith("xpath")


def test_locator_not_found_is_a_debuggable_hard_failure(lookup_artifact, surface, policy, evidence):
    art = lookup_artifact.model_copy(deep=True)
    s02 = next(s for s in art.steps if s.id == "s02")
    s02.target.candidates = [c.model_copy(update={"value": {"role": "button", "name": "Nope"}}) for c in s02.target.candidates if c.strategy == "role"]
    r = _engine(surface, policy, evidence).run(art, {"member_id": "12345"})
    assert r.status == ReplayStatus.ESCALATED_ABORTED and r.outcome_code == "LOCATOR_NOT_FOUND"
    assert r.failed_step == "s02" and "Nope" in r.expected and "url=" in r.observed


def test_llm_is_forbidden_during_replay(lookup_artifact, surface, policy, evidence):
    _engine(surface, policy, evidence).run(lookup_artifact, {"member_id": "12345"})
    from cua.agent.llm import LLMForbidden, AnthropicLLM

    with pytest.raises(LLMForbidden):
        AnthropicLLM()
