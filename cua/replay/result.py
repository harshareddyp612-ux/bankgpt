from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReplayStatus(str, Enum):
    SUCCESS = "SUCCESS"
    BUSINESS_OUTCOME = "BUSINESS_OUTCOME"
    FAILED = "FAILED"
    ESCALATED_ABORTED = "ESCALATED_ABORTED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    INVALID_PARAMS = "INVALID_PARAMS"


class StepResult(BaseModel):
    step_id: str
    action: str
    description: str
    status: str = "pending"
    locator_used: str | None = None
    locator_attempts: list[dict[str, Any]] = Field(default_factory=list)
    attempts: int = 0
    recoveries: list[dict[str, Any]] = Field(default_factory=list)
    confirmed_by: str | None = None
    duration_ms: int = 0
    note: str | None = None


class HumanIntervention(BaseModel):
    step_id: str | None
    kind: str
    reason_code: str
    reason: str
    operator: str
    decision: str
    human_actions: list[dict[str, Any]] = Field(default_factory=list)
    took_s: float = 0.0


class ReplayResult(BaseModel):
    run_id: str
    artifact_id: str
    artifact_name: str
    artifact_version: str
    status: ReplayStatus
    outcome_code: str | None = None
    message: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict, description="Typed outputs (currency -> float, integer -> int)")
    outputs_raw: dict[str, str] = Field(default_factory=dict)
    steps: list[StepResult] = Field(default_factory=list)
    failed_step: str | None = None
    expected: str | None = None
    observed: str | None = None
    human_interventions: list[HumanIntervention] = Field(default_factory=list)
    llm_calls: int = Field(default=0, description="Always 0 on the replay path; asserted by a runtime guard")
    evidence_dir: str = ""
    duration_ms: int = 0

    def brief(self) -> str:
        head = f"{self.status.value}" + (f" ({self.outcome_code})" if self.outcome_code else "")
        lines = [head, f"  {self.message}" if self.message else ""]
        if self.outputs:
            lines.append(f"  outputs: {self.outputs}")
        if self.failed_step:
            lines.append(f"  failed step: {self.failed_step}\n  expected: {self.expected}\n  observed: {self.observed}")
        if self.human_interventions:
            lines.append(f"  human interventions: {[(h.reason_code, h.operator, h.decision) for h in self.human_interventions]}")
        lines.append(f"  steps: " + ", ".join(f"{s.step_id}:{s.status}" + (f"[{s.locator_used.split(':')[0]}]" if s.locator_used else "") for s in self.steps))
        lines.append(f"  evidence: {self.evidence_dir}")
        return "\n".join(l for l in lines if l)
