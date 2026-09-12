import pytest

from cua.artifact.schema import OutcomeDetector
from cua.escalation.control import ControlChannel, ControlState, IllegalTransition
from cua.replay.detectors import DetectionContext, detect


def _ctx(text="", url="http://127.0.0.1:5055/teller/members/search", status=200, overlays=None, step="s02"):
    return DetectionContext(url=url, text=text, http_status=status, overlays=overlays or [], step_id=step)


def test_business_outcome_from_profile(profile):
    outcomes = [OutcomeDetector.model_validate(o) for o in profile["outcomes"]]
    d = detect(outcomes, _ctx(text="Member Lookup No member found matching 99999. Verify the member number and try again."))
    assert d and d.outcome.code == "MEMBER_NOT_FOUND" and d.outcome.kind == "business"
    assert "No member found matching 99999" in d.matched


def test_business_wins_over_hard_when_both_match(profile):
    outcomes = [OutcomeDetector.model_validate(o) for o in profile["outcomes"]]
    d = detect(outcomes, _ctx(text="Access denied ... No member found matching 1"))
    assert d.outcome.kind == "business"


def test_recoverable_overlay_from_profile(profile):
    outcomes = [OutcomeDetector.model_validate(o) for o in profile["outcomes"]]
    d = detect(outcomes, _ctx(text="Member Inquiry", overlays=[{"text": "System Notice Scheduled maintenance"}]))
    assert d.outcome.code == "SYSTEM_NOTICE_DIALOG" and d.outcome.recovery.action == "click"


def test_generic_detectors_when_artifact_has_none():
    assert detect([], _ctx(status=500)).outcome.code == "SERVER_ERROR"
    assert detect([], _ctx(status=403)).outcome.code == "PERMISSION_DENIED"
    assert detect([], _ctx(text="Your session has expired")).outcome.code == "SESSION_EXPIRED"
    d = detect([], _ctx(overlays=[{"text": "Cookie banner"}]))
    assert d.outcome.code == "UNEXPECTED_OVERLAY" and d.outcome.escalate
    assert detect([], _ctx(text="Member Inquiry all good")) is None


def test_applies_to_steps_filter():
    det = OutcomeDetector(code="X", kind="business", description="d", detect={"text_regex": "boom"}, applies_to_steps=["s09"])
    assert detect([det], _ctx(text="boom", step="s02")) is None
    assert detect([det], _ctx(text="boom", step="s09")).outcome.code == "X"


def test_recoverable_requires_recovery():
    with pytest.raises(ValueError):
        OutcomeDetector(code="X", kind="recoverable", description="d", detect={"text_regex": "x"})


def test_control_channel_happy_path():
    ch = ControlChannel()
    seen = []
    ch.on_transition = lambda tr: seen.append((tr.frm, tr.to))
    ch.request_intervention("stuck")
    ch.grant_human()
    assert ch.state == ControlState.HUMAN_IN_CONTROL and ch.owner == "human" and not ch.automation_may_act()
    ch.hand_back()
    ch.resume()
    assert ch.automation_may_act() and len(seen) == 4


def test_control_channel_illegal_transitions():
    ch = ControlChannel()
    with pytest.raises(IllegalTransition):
        ch.grant_human()
    ch.request_intervention("x")
    ch.abort("operator aborted")
    with pytest.raises(IllegalTransition):
        ch.resume()
