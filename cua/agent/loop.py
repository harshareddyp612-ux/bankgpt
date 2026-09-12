from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..artifact.schema import PARAM_TOKEN
from ..escalation.handoff import EscalationManager, InterventionRequest
from ..evidence.logger import RunEvidence
from ..policy.engine import Policy
from ..surface.base import Action, ElementInfo, Observation, Surface, SurfaceError
from .llm import Decision, LLMClient, LLMRefusal, TurnContext
from .prompts import SYSTEM_PROMPT, build_turn
from .tools import TOOL_NAMES, TOOLS


@dataclass
class TraceStep:
    index: int
    tool: str
    args: dict[str, Any]
    reason: str
    url_before: str
    url_after: str
    ok: bool
    message: str = ""
    navigated: bool = False
    extracted: str | None = None
    element: ElementInfo | None = None
    policy: str = ""
    risk: str = "safe"
    confirmed_by: str | None = None
    human_actions: list[dict[str, Any]] = field(default_factory=list)
    screenshot: str | None = None
    model_text: str = ""
    ts: float = field(default_factory=time.time)

    def line(self) -> str:
        target = ""
        if self.element is not None:
            target = f" on [{self.element.index}] {self.element.summary().split(' ', 1)[1][:70]}"
        args = {k: v for k, v in self.args.items() if k not in ("reason", "element_index")}
        status = "ok" if self.ok else f"FAILED ({self.message})"
        nav = f" -> {self.url_after}" if self.navigated else ""
        return f"{self.index}. {self.tool}{target} {args if args else ''} => {status}{nav}"


@dataclass
class DiscoveryResult:
    run_id: str
    success: bool
    stop_reason: str
    goal: str
    target_url: str
    params: dict[str, str]
    sensitive: set[str]
    trace: list[TraceStep]
    outputs: dict[str, str]
    summary: str = ""
    success_text: str = ""
    final_url: str = ""
    model: str = ""
    llm_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    human_interventions: int = 0


class DiscoveryRunner:
    def __init__(
        self,
        surface: Surface,
        llm: LLMClient,
        policy: Policy,
        evidence: RunEvidence,
        escalation: EscalationManager | None = None,
        *,
        max_steps: int = 25,
        max_denials: int = 4,
        stuck_after: int = 4,
    ):
        self.surface = surface
        self.llm = llm
        self.policy = policy
        self.evidence = evidence
        self.escalation = escalation
        self.max_steps = max_steps
        self.max_denials = max_denials
        self.stuck_after = stuck_after

    def run(self, goal: str, target_url: str, params: dict[str, str], sensitive: set[str] | None = None) -> DiscoveryResult:
        sensitive = sensitive or set()
        for name in sensitive:
            if params.get(name):
                self.evidence.redactor.add_sensitive_value(name, params[name])
        params_display = {k: ("<sensitive - substituted locally>" if k in sensitive else v) for k, v in params.items()}
        result = DiscoveryResult(
            run_id=self.evidence.run_id, success=False, stop_reason="", goal=goal, target_url=target_url,
            params=params, sensitive=sensitive, trace=[], outputs={}, model=getattr(self.llm, "name", "unknown"),
        )
        usage_total = {"input_tokens": 0, "output_tokens": 0}
        self.evidence.log("discovery.start", goal=goal, target=target_url, params=params_display, model=result.model, policy=self.policy.describe(), max_steps=self.max_steps)

        entry = Action(type="navigate", options={"url": target_url})
        decision = self.policy.evaluate(entry, None, target_url, "discovery")
        if not decision.allowed:
            result.stop_reason = f"entry blocked by policy: {decision.summary()}"
            return self._finish(result)
        nav = self.surface.goto(target_url)
        if not nav.ok:
            result.stop_reason = f"could not load target: {nav.message}"
            return self._finish(result)

        history: list[str] = []
        feedback: str | None = None
        denials = 0
        fingerprints: list[str] = []
        no_tool_turns = 0

        for step_no in range(1, self.max_steps + 1):
            try:
                obs = self.surface.observe()
            except SurfaceError as e:
                result.stop_reason = f"observation failed: {e}"
                break
            shot = self.evidence.screenshot(f"step-{step_no:02d}", obs.screenshot_png)
            fingerprints.append(obs.fingerprint())
            if len(fingerprints) >= self.stuck_after and len(set(fingerprints[-self.stuck_after :])) == 1:
                if not self._stuck(result, step_no, obs, "DEAD_END", f"screen unchanged for {self.stuck_after} consecutive steps", shot):
                    break
                fingerprints.clear()
                feedback = "A human operator intervened on the live session; re-read the screen."
                continue

            ctx = TurnContext(
                goal=goal, params=params, params_display=params_display, history=history,
                history_structured=[{"tool": t.tool, "args": t.args, "ok": t.ok} for t in result.trace],
                feedback=feedback, obs=obs, step_no=step_no, max_steps=self.max_steps,
            )
            ctx.blocks = build_turn(goal=goal, params_display=params_display, history=history, feedback=feedback, obs=obs,
                                    step_no=step_no, max_steps=self.max_steps, redact=self.evidence.redactor.redact)
            feedback = None
            try:
                decision = self.llm.decide(SYSTEM_PROMPT, ctx, TOOLS)
            except LLMRefusal as e:
                result.stop_reason = f"model refused: {e}"
                self.evidence.log("llm.refusal", error=str(e))
                break
            except RuntimeError as e:
                result.stop_reason = f"llm error: {e}"
                self.evidence.log("llm.error", error=str(e))
                break
            result.llm_calls += 1
            for k in usage_total:
                usage_total[k] += decision.usage.get(k, 0)
            self.evidence.log("llm.decision", step=step_no, tool=decision.tool, args=self._template_args(decision.args, params, sensitive),
                              text=decision.text[:400], usage=decision.usage, model=decision.model, screenshot=shot)

            if decision.tool is None or decision.tool not in TOOL_NAMES:
                no_tool_turns += 1
                feedback = "You must call exactly one tool. Text alone does nothing."
                if no_tool_turns >= 3:
                    result.stop_reason = "model stopped producing actions"
                    break
                continue
            no_tool_turns = 0

            if decision.tool == "done":
                success_text = str(decision.args.get("success_text", "")).strip()
                page_text = self.surface.page_text()
                if success_text and success_text.lower() in page_text.lower():
                    result.success = True
                    result.stop_reason = "goal met"
                    result.summary = str(decision.args.get("summary", ""))
                    result.success_text = success_text
                    result.final_url = obs.url
                    self.evidence.log("discovery.done", summary=result.summary, success_text=success_text, url=obs.url)
                    break
                feedback = f"`done` rejected: success_text {success_text!r} is not visible on the current screen. Pick text that is."
                history.append(f"{step_no}. done => rejected (success_text not visible)")
                continue
            if decision.tool == "request_help":
                if not self._stuck(result, step_no, obs, "AGENT_REQUESTED_HELP", str(decision.args.get("reason", "")), shot):
                    break
                feedback = "A human operator intervened on the live session; re-read the screen and continue."
                history.append(f"{step_no}. request_help => operator intervened")
                continue

            action, element, err = self._to_action(decision, obs, params)
            if err:
                feedback = err
                history.append(f"{step_no}. {decision.tool} => rejected ({err})")
                continue
            pol = self.policy.evaluate(action, element, obs.url, "discovery")
            self.evidence.log("policy.decision", step=step_no, action=action.describe(), decision=pol.summary())
            ts = TraceStep(index=step_no, tool=decision.tool, args=self._template_args(decision.args, params, sensitive), reason=str(decision.args.get("reason", "")),
                           url_before=obs.url, url_after=obs.url, ok=False, element=element, policy=pol.summary(), risk=pol.risk, screenshot=shot, model_text=decision.text[:400])
            if not pol.allowed:
                denials += 1
                ts.message = f"denied by policy: {pol.code}"
                result.trace.append(ts)
                history.append(ts.line())
                feedback = f"Action denied by policy ({pol.code}): {'; '.join(pol.reasons)}. Choose a different action within the allowed application."
                if denials >= self.max_denials:
                    result.stop_reason = "too many policy denials"
                    break
                continue
            if pol.requires_confirmation:
                if self.escalation is None:
                    ts.message = "risky action requires operator confirmation but no operator console is configured"
                    result.trace.append(ts)
                    result.stop_reason = ts.message
                    break
                req = InterventionRequest(run_id=self.evidence.run_id, mode="discovery", kind="confirm_risky", capability=goal, step_ref=f"step {step_no}: {action.describe()}",
                                          reason_code=pol.code or "RISKY", reason="; ".join(pol.reasons), url=obs.url, screenshot=shot, session=self.surface.session_info(),
                                          suggested_actions=["approve", "deny"])
                resp = self.escalation.escalate(req)
                result.human_interventions += 1
                if resp.decision != "resume":
                    ts.message = "risky action denied by operator"
                    result.trace.append(ts)
                    result.stop_reason = "operator denied a risky action"
                    break
                ts.confirmed_by = resp.operator

            resolved = self.surface.handle_for_element(element) if element is not None else None
            res = self.surface.perform(action, resolved)
            ts.ok, ts.message, ts.url_after, ts.navigated, ts.extracted = res.ok, res.message, res.url_after or obs.url, res.navigated, res.extracted
            if decision.tool == "extract" and res.ok:
                name = str(decision.args.get("output_name", "")).strip()
                result.outputs[name] = res.extracted or ""
                if (res.extracted or "").strip() != str(decision.args.get("observed_value", "")).strip():
                    ts.message = f"note: extracted {res.extracted!r} differs from observed_value {decision.args.get('observed_value')!r}"
            result.trace.append(ts)
            history.append(ts.line())
            self.evidence.log("step.result", step=step_no, tool=decision.tool, ok=res.ok, message=res.message, url_after=ts.url_after, navigated=res.navigated, extracted=res.extracted)
            if not res.ok:
                feedback = f"The action failed: {res.message}. If something covers the screen, dismiss it; otherwise choose another element."
        else:
            result.stop_reason = f"max steps ({self.max_steps}) reached"

        if not result.stop_reason:
            result.stop_reason = "stopped"
        result.usage = usage_total
        return self._finish(result)

    def _to_action(self, decision: Decision, obs: Observation, params: dict[str, str]) -> tuple[Action | None, ElementInfo | None, str | None]:
        a = decision.args
        tool = decision.tool
        element: ElementInfo | None = None
        if tool in ("click", "type_text", "select_option", "extract"):
            try:
                element = obs.element(int(a["element_index"]))
            except (KeyError, ValueError, SurfaceError):
                return None, None, f"element_index {a.get('element_index')!r} is not in the current element list"
            if tool != "extract" and element.kind != "interactive":
                return None, None, f"element [{element.index}] is not interactive"
        reason = str(a.get("reason", ""))
        if tool == "click":
            return Action(type="click", element_index=element.index, reason=reason), element, None
        if tool == "type_text":
            text = PARAM_TOKEN.sub(lambda m: params.get(m.group(1), m.group(0)), str(a.get("text", "")))
            return Action(type="type", element_index=element.index, text=text, options={"press_enter": bool(a.get("press_enter")), "clear": True}, reason=reason), element, None
        if tool == "select_option":
            return Action(type="select", element_index=element.index, options={"option_label": str(a.get("option_label", ""))}, reason=reason), element, None
        if tool == "extract":
            return Action(type="extract", element_index=element.index, options={"output_name": str(a.get("output_name", ""))}, reason=reason), element, None
        if tool == "press_key":
            return Action(type="press", options={"key": str(a.get("key", "Enter"))}, reason=reason), None, None
        if tool == "navigate":
            return Action(type="navigate", options={"url": str(a.get("url", ""))}, reason=reason), None, None
        if tool == "scroll":
            return Action(type="scroll", options={"direction": str(a.get("direction", "down"))}, reason=reason), None, None
        if tool == "wait":
            return Action(type="wait", options={"ms": int(a.get("seconds", 1)) * 1000}, reason=reason), None, None
        return None, None, f"unknown tool {tool}"

    @staticmethod
    def _template_args(args: dict[str, Any], params: dict[str, str], sensitive: set[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(v, str):
                for name, val in sorted(params.items(), key=lambda kv: -len(kv[1])):
                    if val and len(val) >= 2 and val in v:
                        v = v.replace(val, f"{{{{{name}}}}}")
            out[k] = v
        return out

    def _stuck(self, result: DiscoveryResult, step_no: int, obs: Observation, code: str, reason: str, shot: str | None) -> bool:
        self.evidence.log("discovery.stuck", step=step_no, code=code, reason=reason)
        if self.escalation is None:
            result.stop_reason = f"stuck ({code}): {reason} - no operator console configured"
            return False
        req = InterventionRequest(run_id=self.evidence.run_id, mode="discovery", kind="stuck", capability=result.goal, step_ref=f"step {step_no}",
                                  reason_code=code, reason=reason, url=obs.url, screenshot=shot, session=self.surface.session_info(),
                                  suggested_actions=["complete the blocking step manually, then resume", "abort"])
        resp = self.escalation.escalate(req)
        result.human_interventions += 1
        result.trace.append(TraceStep(index=step_no, tool="human", args={"kind": "stuck", "code": code}, reason=reason, url_before=obs.url,
                                      url_after=self.surface.current_url(), ok=resp.decision == "resume", message=f"operator {resp.operator}: {resp.decision}",
                                      human_actions=resp.human_actions, screenshot=shot))
        if resp.decision != "resume":
            result.stop_reason = f"stuck ({code}); operator aborted"
            return False
        return True

    def _finish(self, result: DiscoveryResult) -> DiscoveryResult:
        self.evidence.write_json("trace.json", [self._trace_dump(t) for t in result.trace])
        self.evidence.finalize({"kind": "discovery", "success": result.success, "stop_reason": result.stop_reason, "goal": result.goal, "model": result.model,
                                "llm_calls": result.llm_calls, "usage": result.usage, "outputs": result.outputs, "steps": len(result.trace),
                                "human_interventions": result.human_interventions})
        self.evidence.log("discovery.end", success=result.success, stop_reason=result.stop_reason, llm_calls=result.llm_calls, usage=result.usage)
        return result

    @staticmethod
    def _trace_dump(t: TraceStep) -> dict[str, Any]:
        d = asdict(t)
        if t.element is not None:
            d["element"] = {"index": t.element.index, "tag": t.element.tag, "role": t.element.role, "name": t.element.name, "text": t.element.text[:80],
                            "candidates": [c.model_dump() for c in t.element.candidates]}
        return d
