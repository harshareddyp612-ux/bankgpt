import json

import pytest
from pydantic import ValidationError

from cua.artifact.schema import Artifact, Checkpoint, Locator, LocatorCandidate, Output, Parameter, Step, Target, WaitCondition


def _loc(desc="Search button"):
    return Locator(description=desc, candidates=[
        LocatorCandidate(strategy="xpath", value={"xpath": "/html/body/input[1]"}, confidence=0.3),
        LocatorCandidate(strategy="role", value={"role": "button", "name": "Search"}, confidence=0.9),
    ])


def _artifact(**over):
    base = dict(
        id="a1", name="lookup", version="1.0.0", description="d", goal="g {{member_id}}", created_at="now",
        target=Target(app_id="app", base_url="http://127.0.0.1:5055", entry_path="/teller/members/search"),
        inputs=[Parameter(name="member_id", pattern=r"^\d+$")],
        outputs=[Output(name="balance", type="currency", source_step="s02")],
        steps=[
            Step(id="s01", action="type", description="type", target=_loc("Member box"), input="{{member_id}}"),
            Step(id="s02", action="extract", description="read", target=_loc("cell"), output="balance"),
        ],
        checkpoint=Checkpoint(description="done", conditions=[WaitCondition(kind="text_visible", value="Member Inquiry")]),
    )
    base.update(over)
    return Artifact(**base)


def test_candidates_are_sorted_by_confidence():
    loc = _loc()
    assert [c.strategy for c in loc.candidates] == ["role", "xpath"]


def test_roundtrip_json():
    a = _artifact()
    b = Artifact.model_validate(json.loads(a.model_dump_json()))
    assert b == a


def test_undeclared_parameter_rejected():
    with pytest.raises(ValidationError, match="undeclared parameters"):
        _artifact(steps=[Step(id="s01", action="type", description="t", target=_loc(), input="{{nope}}"),
                         Step(id="s02", action="extract", description="r", target=_loc(), output="balance")])


def test_output_must_come_from_extract_step():
    with pytest.raises(ValidationError, match="must be produced by an extract step"):
        _artifact(outputs=[Output(name="balance", source_step="s01")])


def test_step_shape_rules():
    with pytest.raises(ValidationError, match="requires a target"):
        Step(id="x", action="click", description="c")
    with pytest.raises(ValidationError, match="requires input"):
        Step(id="x", action="type", description="t", target=_loc())
    with pytest.raises(ValidationError, match="requires options.url"):
        Step(id="x", action="navigate", description="n")


def test_validate_params_contract():
    a = _artifact()
    assert a.validate_params({"member_id": 12345}) == {"member_id": "12345"}
    with pytest.raises(ValueError, match="missing required"):
        a.validate_params({})
    with pytest.raises(ValueError, match="does not match pattern"):
        a.validate_params({"member_id": "abc"})
    with pytest.raises(ValueError, match="unknown parameters"):
        a.validate_params({"member_id": "1", "extra": "x"})


def test_render_and_tenant_overrides():
    a = _artifact()
    assert Artifact.render("id={{member_id}}", {"member_id": "7"}) == "id=7"
    a.tenant.step_overrides = {"s01": {"description": "tenant-specific"}}
    eff = a.effective_steps()
    assert eff[0].description == "tenant-specific" and a.steps[0].description == "type"


def test_tool_definition_shape():
    td = _artifact().tool_definition()
    assert td["name"] == "lookup"
    assert td["input_schema"]["required"] == ["member_id"]
    assert td["input_schema"]["properties"]["member_id"]["pattern"] == r"^\d+$"
    assert "balance (currency)" in td["description"]


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        Parameter(name="x", bogus=1)
