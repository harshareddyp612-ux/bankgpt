from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReplaySignal(Exception):
    code: str
    message: str
    step_id: str | None = None
    expected: str | None = None
    observed: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass
class BusinessOutcome(ReplaySignal):
    pass


@dataclass
class Recoverable(ReplaySignal):
    pass


@dataclass
class HardFailure(ReplaySignal):
    escalate: bool = False


@dataclass
class PolicyBlocked(ReplaySignal):
    pass


@dataclass
class EscalationAborted(ReplaySignal):
    pass


LOCATOR_NOT_FOUND = "LOCATOR_NOT_FOUND"
ACTION_FAILED = "ACTION_FAILED"
POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
CHECKPOINT_FAILED = "CHECKPOINT_FAILED"
NAVIGATION_FAILED = "NAVIGATION_FAILED"
STEP_RETRIES_EXHAUSTED = "STEP_RETRIES_EXHAUSTED"
RECOVERY_EXHAUSTED = "RECOVERY_EXHAUSTED"
OUTPUT_MISSING = "OUTPUT_MISSING"
