from __future__ import annotations

import abc
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from ..evidence.logger import RunEvidence
from ..surface.base import Surface
from .control import ControlChannel


@dataclass
class InterventionRequest:
    run_id: str
    mode: str
    kind: str
    capability: str
    step_ref: str
    reason_code: str
    reason: str
    url: str
    screenshot: str | None
    session: dict[str, Any]
    suggested_actions: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def render(self) -> str:
        lines = [
            "",
            "┌──────────────────────────── INTERVENTION REQUESTED ────────────────────────────",
            f"│ run:        {self.run_id}  ({self.mode})",
            f"│ capability: {self.capability}",
            f"│ step:       {self.step_ref}",
            f"│ kind:       {self.kind}   code: {self.reason_code}",
            f"│ why:        {self.reason}",
            f"│ url:        {self.url}",
            f"│ screenshot: {self.screenshot}",
            f"│ session:    {self.session}",
        ]
        if self.suggested_actions:
            lines.append("│ suggested:  " + " | ".join(self.suggested_actions))
        lines.append("└────────────────────────────────────────────────────────────────────────────────")
        return "\n".join(lines)


@dataclass
class InterventionResponse:
    decision: str
    operator: str
    notes: str = ""
    human_actions: list[dict[str, Any]] = field(default_factory=list)
    took_s: float = 0.0


class OperatorConsole(abc.ABC):

    name = "abstract"

    @abc.abstractmethod
    def handle(self, request: InterventionRequest, surface: Surface) -> str:
        ...


class NoOperatorConsole(OperatorConsole):

    name = "none"

    def handle(self, request: InterventionRequest, surface: Surface) -> str:
        return "abort"


class CliOperatorConsole(OperatorConsole):

    name = "cli"

    def __init__(self, input_fn: Callable[[str], str] = input, print_fn: Callable[[str], None] = print):
        self._input = input_fn
        self._print = print_fn

    def handle(self, request: InterventionRequest, surface: Surface) -> str:
        self._print(request.render())
        if request.kind == "confirm_risky":
            self._print("│ A RISKY action is about to be performed. Type 'approve' to allow it, 'deny' to stop.")
        else:
            self._print("│ You now control the live browser window. Perform the manual steps there, then")
            self._print("│ type 'resume' to hand control back, or 'abort' to stop the run.")
        while True:
            ans = (self._input("operator> ") or "").strip().lower()
            if ans in ("resume", "approve", "r", "a", "y", "yes"):
                return "resume"
            if ans in ("abort", "deny", "n", "no", "q"):
                return "abort"
            self._print("│ please type resume/approve or abort/deny")


class ScriptedOperatorConsole(OperatorConsole):

    name = "scripted"

    def __init__(self, name: str, script: Callable[[InterventionRequest, Surface], str]):
        self.name = f"scripted:{name}"
        self._script = script

    def handle(self, request: InterventionRequest, surface: Surface) -> str:
        return self._script(request, surface)


class EscalationManager:
    def __init__(self, channel: ControlChannel, console: OperatorConsole, evidence: RunEvidence, surface: Surface):
        self.channel = channel
        self.console = console
        self.evidence = evidence
        self.surface = surface
        self.interventions: list[dict[str, Any]] = []
        channel.on_transition = lambda tr: evidence.log(
            "control.transition", frm=tr.frm.value, to=tr.to.value, reason=tr.reason, owner=channel.owner
        )

    def escalate(self, request: InterventionRequest) -> InterventionResponse:
        t0 = time.time()
        self.channel.request_intervention(f"{request.kind}:{request.reason_code} - {request.reason}")
        self.evidence.log("intervention.requested", request=asdict(request), operator_console=self.console.name)

        self.channel.grant_human(f"operator console '{self.console.name}' engaged")
        self.surface.begin_human_control()
        try:
            decision = self.console.handle(request, self.surface)
        except Exception as e:
            decision = "abort"
            self.evidence.log("intervention.console_error", error=str(e))
        human_actions = self.surface.end_human_control()

        if decision == "resume":
            self.channel.hand_back("operator handed control back")
            after = self.evidence.screenshot(f"handoff-after-{len(self.interventions) + 1:02d}", self.surface.screenshot())
            self.evidence.log(
                "intervention.resolved",
                decision="resume",
                operator=self.console.name,
                human_actions=human_actions,
                url_after=self.surface.current_url(),
                screenshot_after=after,
            )
            self.channel.resume()
        else:
            self.evidence.log("intervention.resolved", decision="abort", operator=self.console.name, human_actions=human_actions)
            self.channel.abort(f"operator '{self.console.name}' aborted the run")

        resp = InterventionResponse(decision=decision, operator=self.console.name, human_actions=human_actions, took_s=round(time.time() - t0, 2))
        self.interventions.append({"request": asdict(request), "response": asdict(resp)})
        return resp
