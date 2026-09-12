from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"

LocatorStrategy = Literal[
    "role",
    "label",
    "placeholder",
    "near_text",
    "table_cell",
    "text",
    "css",
    "xpath",
    "coords",
]

ActionType = Literal["navigate", "click", "type", "select", "press", "scroll", "extract", "wait"]
RiskLevel = Literal["safe", "risky"]
OutcomeKind = Literal["business", "recoverable", "hard"]

PARAM_TOKEN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocatorCandidate(StrictModel):
    strategy: LocatorStrategy
    value: dict[str, Any] = Field(description="Strategy-specific payload, e.g. {'role': 'button', 'name': 'Search'}")
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class Locator(StrictModel):
    description: str = Field(description="Human-readable name of the control, e.g. 'Member Number text box'")
    frame_path: list[str] = Field(default_factory=list, description="Frame name/url chain from the main frame; [] = main frame")
    candidates: list[LocatorCandidate] = Field(min_length=1)
    recorded: dict[str, Any] | None = Field(
        default=None, description="What the control looked like at record time (tag/role/name/text) for drift diagnosis"
    )

    @field_validator("candidates")
    @classmethod
    def _sorted_by_confidence(cls, v: list[LocatorCandidate]) -> list[LocatorCandidate]:
        return sorted(v, key=lambda c: -c.confidence)


class WaitCondition(StrictModel):
    kind: Literal["url_matches", "text_visible", "text_absent", "element_visible", "load_state", "sleep_ms"]
    value: str | int | dict[str, Any]
    timeout_ms: int = 10_000
    description: str = ""


class Step(StrictModel):
    id: str
    action: ActionType
    description: str
    target: Locator | None = None
    input: str | None = Field(default=None, description="Literal text, or a template like '{{member_id}}'")
    options: dict[str, Any] = Field(default_factory=dict, description="press_enter, clear, option_label, key, url, direction ...")
    expect: list[WaitCondition] = Field(default_factory=list, description="Post-conditions verified after the action")
    risk: RiskLevel = "safe"
    risk_reason: str | None = None
    output: str | None = Field(default=None, description="For extract steps: the output name this step populates")

    @model_validator(mode="after")
    def _shape(self) -> "Step":
        if self.action in {"click", "type", "select", "extract"} and self.target is None:
            raise ValueError(f"step {self.id}: action '{self.action}' requires a target")
        if self.action == "type" and self.input is None:
            raise ValueError(f"step {self.id}: type action requires input")
        if self.action == "extract" and not self.output:
            raise ValueError(f"step {self.id}: extract action requires an output name")
        if self.action == "navigate" and "url" not in self.options:
            raise ValueError(f"step {self.id}: navigate requires options.url")
        return self

    def param_refs(self) -> set[str]:
        refs: set[str] = set()
        for text in [self.input, self.options.get("url"), self.options.get("option_label")]:
            if isinstance(text, str):
                refs.update(PARAM_TOKEN.findall(text))
        return refs


class Parameter(StrictModel):
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    type: Literal["string", "integer", "number", "boolean"] = "string"
    description: str = ""
    required: bool = True
    example: str | None = None
    pattern: str | None = Field(default=None, description="Regex the value must match (validated before replay starts)")
    sensitive: bool = Field(default=False, description="Never logged or stored; substituted locally at type time")


class Output(StrictModel):
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    type: Literal["string", "integer", "number", "boolean", "currency"] = "string"
    description: str = ""
    source_step: str
    example: str | None = None


class Recovery(StrictModel):
    action: Literal["click", "wait_retry", "reload"]
    target: Locator | None = None
    max_attempts: int = 2
    delay_ms: int = 1500
    description: str = ""


class OutcomeDetector(StrictModel):

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    kind: OutcomeKind
    description: str
    detect: dict[str, Any] = Field(description="Any of: text_regex, url_regex, http_status, overlay_text_regex")
    recovery: Recovery | None = None
    escalate: bool = False
    applies_to_steps: list[str] | None = Field(default=None, description="Restrict to step ids; None = any step")

    @model_validator(mode="after")
    def _shape(self) -> "OutcomeDetector":
        if not any(k in self.detect for k in ("text_regex", "url_regex", "http_status", "overlay_text_regex")):
            raise ValueError(f"outcome {self.code}: detect needs text_regex, url_regex, http_status or overlay_text_regex")
        if self.kind == "recoverable" and self.recovery is None:
            raise ValueError(f"outcome {self.code}: recoverable outcomes need a recovery")
        return self


class Checkpoint(StrictModel):
    description: str
    conditions: list[WaitCondition] = Field(min_length=1)


class Target(StrictModel):
    app_id: str = Field(description="Vendor product identifier, shared by all tenants running it")
    surface: Literal["web", "legacy_web", "desktop"] = "legacy_web"
    base_url: str
    entry_path: str = "/"
    vendor: str | None = None
    app_version: str | None = None


class TenantBinding(StrictModel):

    tenant_id: str | None = None
    app_version: str | None = None
    step_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict, description="step_id -> partial Step fields")


class Artifact(StrictModel):
    schema_version: str = SCHEMA_VERSION
    id: str
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", description="Capability name an agent invokes")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str
    goal: str = Field(description="The natural-language goal, with parameter tokens")
    created_at: str
    created_from_run: str | None = None
    approval: Literal["draft", "approved", "deprecated"] = "draft"
    target: Target
    tenant: TenantBinding = Field(default_factory=TenantBinding)
    inputs: list[Parameter] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    checkpoint: Checkpoint
    outcomes: list[OutcomeDetector] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cross_refs(self) -> "Artifact":
        step_ids = [s.id for s in self.steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("duplicate step ids")
        param_names = {p.name for p in self.inputs}
        for s in self.steps:
            missing = s.param_refs() - param_names
            if missing:
                raise ValueError(f"step {s.id} references undeclared parameters: {sorted(missing)}")
        for o in self.outputs:
            if o.source_step not in step_ids:
                raise ValueError(f"output {o.name} references unknown step {o.source_step}")
            src = next(s for s in self.steps if s.id == o.source_step)
            if src.action != "extract" or src.output != o.name:
                raise ValueError(f"output {o.name} must be produced by an extract step with output={o.name}")
        for od in self.outcomes:
            for sid in od.applies_to_steps or []:
                if sid not in step_ids:
                    raise ValueError(f"outcome {od.code} references unknown step {sid}")
        return self

    def input_by_name(self, name: str) -> Parameter | None:
        return next((p for p in self.inputs if p.name == name), None)

    def validate_params(self, params: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for p in self.inputs:
            if p.name not in params or params[p.name] in (None, ""):
                if p.required:
                    raise ValueError(f"missing required parameter '{p.name}'")
                continue
            v = str(params[p.name])
            if p.type == "integer" and not re.fullmatch(r"-?\d+", v):
                raise ValueError(f"parameter '{p.name}' must be an integer")
            if p.type == "number" and not re.fullmatch(r"-?\d+(\.\d+)?", v):
                raise ValueError(f"parameter '{p.name}' must be a number")
            if p.type == "boolean" and v.lower() not in {"true", "false"}:
                raise ValueError(f"parameter '{p.name}' must be true/false")
            if p.pattern and not re.fullmatch(p.pattern, v):
                raise ValueError(f"parameter '{p.name}' does not match pattern {p.pattern}")
            out[p.name] = v
        unknown = set(params) - {p.name for p in self.inputs}
        if unknown:
            raise ValueError(f"unknown parameters: {sorted(unknown)}")
        return out

    @staticmethod
    def render(template: str | None, params: dict[str, str]) -> str | None:
        if template is None:
            return None
        return PARAM_TOKEN.sub(lambda m: params.get(m.group(1), m.group(0)), template)

    def effective_steps(self) -> list[Step]:
        if not self.tenant.step_overrides:
            return list(self.steps)
        out = []
        for s in self.steps:
            ov = self.tenant.step_overrides.get(s.id)
            out.append(s.model_copy(update=ov) if ov else s)
        return out

    def tool_definition(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for p in self.inputs:
            js_type = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean"}[p.type]
            props[p.name] = {"type": js_type, "description": p.description}
            if p.pattern:
                props[p.name]["pattern"] = p.pattern
        return {
            "name": self.name,
            "description": self.description
            + "\nReturns: "
            + ", ".join(f"{o.name} ({o.type})" for o in self.outputs)
            + "\nKnown outcomes: "
            + ", ".join(o.code for o in self.outcomes if o.kind == "business"),
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": [p.name for p in self.inputs if p.required],
                "additionalProperties": False,
            },
        }
