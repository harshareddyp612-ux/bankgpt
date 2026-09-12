from cua.policy.engine import Policy
from cua.policy.redact import Redactor
from cua.surface.base import Action, ElementInfo

BASE = "http://127.0.0.1:5055"


def _el(**kw):
    d = dict(index=1, kind="interactive", tag="input", role="button", name="Search", text="Search", attrs={"type": "submit"},
             bbox={"x": 0, "y": 0, "w": 10, "h": 10}, xpath="/html/body/input[1]")
    d.update(kw)
    return ElementInfo(**d)


def test_allowlist_hosts_and_paths(policy: Policy):
    assert policy.url_allowed(f"{BASE}/teller/members/search")[0]
    assert not policy.url_allowed("https://evil.example.com/teller/members/search")[0]
    assert not policy.url_allowed(f"{BASE}/__chaos/denied")[0]
    assert not policy.url_allowed(f"{BASE}/admin/users")[0]


def test_navigation_outside_allowlist_denied(policy: Policy):
    d = policy.evaluate(Action(type="navigate", options={"url": "https://vendor-help.example.com/"}), None, f"{BASE}/teller/members/search", "discovery")
    assert not d.allowed and d.code == "NAVIGATION_BLOCKED"


def test_link_click_to_external_target_denied(policy: Policy):
    el = _el(tag="a", role="link", name="Help", attrs={"href": "https://vendor-help.example.com/kb"})
    d = policy.evaluate(Action(type="click", element_index=1), el, f"{BASE}/teller/members/12345", "discovery")
    assert not d.allowed and d.code == "LINK_BLOCKED"


def test_disallowed_action_type(policy: Policy):
    d = policy.evaluate(Action(type="upload"), None, f"{BASE}/teller/members/search", "replay")
    assert not d.allowed and d.code == "ACTION_NOT_ALLOWED"


def test_safe_submit_is_allowed(policy: Policy):
    d = policy.evaluate(Action(type="click", element_index=1), _el(name="Search"), f"{BASE}/teller/members/search", "discovery")
    assert d.allowed and d.risk == "safe" and not d.requires_confirmation


def test_risky_button_requires_confirmation_in_discovery(policy: Policy):
    d = policy.evaluate(Action(type="click", element_index=1), _el(name="Open Sub-Account"), f"{BASE}/teller/members/12345/subaccounts/new", "discovery")
    assert d.allowed and d.risk == "risky" and d.requires_confirmation


def test_link_named_like_a_commit_is_not_risky(policy: Policy):
    el = _el(tag="a", role="link", name="Open Sub-Account", attrs={"href": "/teller/members/12345/subaccounts/new"})
    d = policy.evaluate(Action(type="click", element_index=1), el, f"{BASE}/teller/members/12345", "discovery")
    assert d.risk == "safe"


def test_replay_risky_gating(policy: Policy):
    rec = {"role": "button", "name": "Open Sub-Account"}
    url = f"{BASE}/teller/members/12345/subaccounts/new"
    a = Action(type="click")
    d = policy.evaluate(a, None, url, "replay", artifact_approved=False, confirm_risky=False, recorded=rec)
    assert d.requires_confirmation and d.code == "RISKY_NEEDS_CONFIRMATION"
    d = policy.evaluate(a, None, url, "replay", artifact_approved=True, confirm_risky=False, recorded=rec)
    assert d.requires_confirmation
    d = policy.evaluate(a, None, url, "replay", artifact_approved=True, confirm_risky=True, recorded=rec)
    assert d.allowed and not d.requires_confirmation and d.risk == "risky"


def test_enter_on_risky_route_is_risky(policy: Policy):
    d = policy.evaluate(Action(type="press", options={"key": "Enter"}), None, f"{BASE}/teller/members/12345/subaccounts/new", "replay", recorded={})
    assert d.risk == "risky"


def test_redaction_patterns(policy: Policy):
    r = policy.redactor
    out = r.redact("ssn 123-45-6789 card 4111 1111 1111 1111 key sk-ant-abc_123 password=hunter2 run replay-20260911-230402-d10248")
    assert "123-45-6789" not in out and "[CARD]" in out and "[API_KEY]" in out and "hunter2" not in out
    assert "replay-20260911-230402-d10248" in out


def test_sensitive_values_masked_everywhere():
    r = Redactor()
    r.add_sensitive_value("password", "demo-pass")
    assert r.redact_obj({"a": "typed demo-pass", "b": ["demo-pass", 1]}) == {"a": "typed [REDACTED:password]", "b": ["[REDACTED:password]", 1]}
