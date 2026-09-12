from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class ControlState(str, Enum):
    AUTOMATION = "AUTOMATION"
    INTERVENTION_REQUESTED = "INTERVENTION_REQUESTED"
    HUMAN_IN_CONTROL = "HUMAN_IN_CONTROL"
    RESUMING = "RESUMING"
    ABORTED = "ABORTED"


_ALLOWED = {
    ControlState.AUTOMATION: {ControlState.INTERVENTION_REQUESTED},
    ControlState.INTERVENTION_REQUESTED: {ControlState.HUMAN_IN_CONTROL, ControlState.ABORTED, ControlState.RESUMING},
    ControlState.HUMAN_IN_CONTROL: {ControlState.RESUMING, ControlState.ABORTED},
    ControlState.RESUMING: {ControlState.AUTOMATION, ControlState.ABORTED},
    ControlState.ABORTED: set(),
}


class IllegalTransition(Exception):
    pass


@dataclass
class Transition:
    frm: ControlState
    to: ControlState
    reason: str
    ts: float


@dataclass
class ControlChannel:
    state: ControlState = ControlState.AUTOMATION
    owner: str = "automation"
    history: list[Transition] = field(default_factory=list)
    on_transition: Callable[[Transition], None] | None = None

    def _go(self, to: ControlState, reason: str) -> None:
        if to not in _ALLOWED[self.state]:
            raise IllegalTransition(f"{self.state.value} -> {to.value} not allowed ({reason})")
        tr = Transition(self.state, to, reason, time.time())
        self.state = to
        self.owner = {
            ControlState.AUTOMATION: "automation",
            ControlState.INTERVENTION_REQUESTED: "automation(paused)",
            ControlState.HUMAN_IN_CONTROL: "human",
            ControlState.RESUMING: "automation(verifying)",
            ControlState.ABORTED: "nobody",
        }[to]
        self.history.append(tr)
        if self.on_transition:
            self.on_transition(tr)

    def request_intervention(self, reason: str) -> None:
        self._go(ControlState.INTERVENTION_REQUESTED, reason)

    def grant_human(self, reason: str = "operator accepted") -> None:
        self._go(ControlState.HUMAN_IN_CONTROL, reason)

    def hand_back(self, reason: str = "operator handed control back") -> None:
        self._go(ControlState.RESUMING, reason)

    def resume(self, reason: str = "state verified, automation resumes") -> None:
        self._go(ControlState.AUTOMATION, reason)

    def abort(self, reason: str) -> None:
        self._go(ControlState.ABORTED, reason)

    def automation_may_act(self) -> bool:
        return self.state == ControlState.AUTOMATION
