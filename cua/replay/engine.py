from __future__ import annotations

import os
import re
import time
from dataclasses import asdict
from typing import Any
from urllib.parse import urljoin

from ..artifact.schema import Artifact, Locator, Recovery, Step, WaitCondition
from ..escalation.handoff import EscalationManager, InterventionRequest
from ..evidence.logger import RunEvidence
from ..policy.engine import Policy
from ..surface.base import Action, Resolved, Surface
from . import errors as E
from .detectors import Detection, DetectionContext, detect
from .locators import resolve_locator
from .result import HumanIntervention, ReplayResult, ReplayStatus, StepResult

_DISMISS_RX = re.compile(r"(?i)^(dismiss|ok|okay|close|continue|acknowledge|got it|accept|×|x)$")


class ReplayEngine:
    def __init__(
        self,
        surface: Surface,
        policy: Policy,
        evidence: RunEvidence,
        escalation: EscalationManager | None = None,
        *,
        confirm_risky: bool = False,
        allow_coords: bool = False,
        max_step_attempts: int = 3,
    ):
        self.surface = surface
        self.policy = policy
        self.evidence = evidence
        self.escalation = escalation
        self.confirm_risky = confirm_risky
        self.allow_coords = allow_coords
        self.max_step_attempts = max_step_attempts
        self._recovery_counts: dict[str, int] = {}
        self._interventions: list[HumanIntervention] = []
        self._outputs_raw: dict[str, str] = {}
        self._artifact: Artifact | None = None
        self._params: dict[str, str] = {}
        self._shot = 0

    def run(self, artifact: Artifact, params: dict[str, Any]) -> ReplayResult:
        os.environ["CUA_NO_LLM"] = "1"
        t0 = time.time()
        self._artifact = artifact
        result = ReplayResult(
            run_id=self.evidence.run_id,
            artifact_id=artifact.id,
            artifact_name=artifact.name,
            artifact_version=artifact.version,
            status=ReplayStatus.FAILED,
            evidence_dir=str(self.evidence.dir),
        )
        try:
            self._params = artifact.validate_params(params)
        except ValueError as e:
            result.status, result.outcome_code, result.message = ReplayStatus.INVALID_PARAMS, "INVALID_PARAMS", str(e)
            self.evidence.log("replay.invalid_params", error=str(e))
            return self._finish(result, t0)
        for p in artifact.inputs:
            if p.sensitive and p.name in self._params:
                self.evidence.redactor.add_sensitive_value(p.name, self._params[p.name])
        self.evidence.log(
            "replay.start",
            artifact={"id": artifact.id, "name": artifact.name, "version": artifact.version, "approval": artifact.approval},
            params=self._params,
            policy=self.policy.describe(),
            confirm_risky=self.confirm_risky,
            operator=self.escalation.console.name if self.escalation else None,
        )

        try:
            self._enter(artifact)
            for step in artifact.effective_steps():
                result.steps.append(self._run_step(step))
            self._verify_checkpoint(artifact)
            result.outputs_raw = dict(self._outputs_raw)
            result.outputs = self._typed_outputs(artifact)
            result.status = ReplayStatus.SUCCESS
            result.message = f"checkpoint verified: {artifact.checkpoint.description}"
        except E.BusinessOutcome as b:
            result.status, result.outcome_code, result.message = ReplayStatus.BUSINESS_OUTCOME, b.code, b.message
            result.failed_step, result.expected, result.observed = b.step_id, b.expected, b.observed
            result.outputs_raw = dict(self._outputs_raw)
        except E.PolicyBlocked as p:
            result.status, result.outcome_code, result.message = ReplayStatus.POLICY_BLOCKED, p.code, p.message
            result.failed_step = p.step_id
        except E.EscalationAborted as a:
            result.status, result.outcome_code, result.message = ReplayStatus.ESCALATED_ABORTED, a.code, a.message
            result.failed_step, result.expected, result.observed = a.step_id, a.expected, a.observed
        except E.HardFailure as h:
            result.status, result.outcome_code, result.message = ReplayStatus.FAILED, h.code, h.message
            result.failed_step, result.expected, result.observed = h.step_id, h.expected, h.observed
        except Exception as ex:
            result.status, result.outcome_code, result.message = ReplayStatus.FAILED, "INTERNAL_ERROR", f"{type(ex).__name__}: {ex}"
            self.evidence.log("replay.internal_error", error=repr(ex))
        result.human_interventions = list(self._interventions)
        return self._finish(result, t0)

    def _enter(self, artifact: Artifact) -> None:
        url = urljoin(artifact.target.base_url, Artifact.render(artifact.target.entry_path, self._params) or "/")
        action = Action(type="navigate", options={"url": url})
        decision = self.policy.evaluate(action, None, url, "replay", artifact_approved=artifact.approval == "approved", confirm_risky=self.confirm_risky)
        self.evidence.log("policy.decision", step="entry", action="navigate", url=url, decision=decision.summary())
        if not decision.allowed:
            raise E.PolicyBlocked(decision.code or "POLICY_BLOCKED", "; ".join(decision.reasons), step_id="entry")
        res = self.surface.goto(url)
        self._snap("entry")
        if not res.ok:
            self._escalate_or_fail(E.HardFailure(E.NAVIGATION_FAILED, res.message, step_id="entry", expected=url, observed=res.url_after, escalate=True), None, None)
        det = self._detect(None)
        if det:
            self._apply_detection(det, None, None)

    def _run_step(self, step: Step) -> StepResult:
        assert self._artifact is not None
        sr = StepResult(step_id=step.id, action=step.action, description=step.description)
        t0 = time.time()
        action = self._action_for(step)
        while True:
            sr.attempts += 1
            if sr.attempts > self.max_step_attempts:
                raise E.HardFailure(E.STEP_RETRIES_EXHAUSTED, f"step {step.id} failed after {self.max_step_attempts} attempts", step_id=step.id, expected=step.description, observed=self._observed())
            self.evidence.log("step.begin", step=step.id, attempt=sr.attempts, action=step.action, description=step.description, url=self.surface.current_url())

            url = self.surface.current_url()
            decision = self.policy.evaluate(
                action, None, url, "replay",
                artifact_approved=self._artifact.approval == "approved", confirm_risky=self.confirm_risky,
                recorded=(step.target.recorded if step.target else None),
            )
            self.evidence.log("policy.decision", step=step.id, decision=decision.summary())
            if not decision.allowed:
                raise E.PolicyBlocked(decision.code or "POLICY_BLOCKED", "; ".join(decision.reasons), step_id=step.id)
            if decision.requires_confirmation:
                resp = self._escalate(
                    kind="confirm_risky", step=step, reason_code=decision.code or "RISKY_NEEDS_CONFIRMATION",
                    reason=f"risky step '{step.description}': " + "; ".join(decision.reasons),
                    suggested=["approve to perform the step", "deny to stop the run"],
                )
                sr.confirmed_by = resp.operator

            resolved: Resolved | None = None
            if step.target is not None:
                resolved, tried = resolve_locator(self.surface, step.target, self._params, allow_coords=self.allow_coords)
                sr.locator_attempts = tried
                if resolved is None:
                    self.evidence.log("locator.miss", step=step.id, target=step.target.description, tried=tried)
                    if self._handle_detected(step, sr):
                        if self._step_satisfied(step):
                            return self._done(sr, "recovered", t0)
                        continue
                    self._escalate_or_fail(
                        E.HardFailure(E.LOCATOR_NOT_FOUND, f"could not locate '{step.target.description}' with any of {len(step.target.candidates)} candidates",
                                      step_id=step.id, expected=self._expected_target(step.target), observed=self._observed(), escalate=True),
                        step, sr,
                    )
                    if self._step_satisfied(step):
                        return self._done(sr, "human", t0)
                    continue
                sr.locator_used = f"{resolved.strategy}:{resolved.detail}" if resolved.detail else resolved.strategy
                self.evidence.log("locator.resolved", step=step.id, strategy=resolved.strategy, detail=resolved.detail, tried=len(tried))

            res = self.surface.perform(action, resolved)
            self._snap(f"step-{step.id}")
            self.evidence.log("step.performed", step=step.id, ok=res.ok, url_after=res.url_after, navigated=res.navigated, message=res.message, error_kind=res.error_kind,
                              extracted=res.extracted)
            if not res.ok:
                if self._handle_detected(step, sr):
                    continue
                self._escalate_or_fail(
                    E.HardFailure(E.ACTION_FAILED, f"{step.action} failed: {res.message}", step_id=step.id, expected=step.description, observed=self._observed(), escalate=True),
                    step, sr,
                )
                if self._step_satisfied(step):
                    return self._done(sr, "human", t0)
                continue
            if step.action == "extract" and step.output:
                self._outputs_raw[step.output] = res.extracted or ""

            det_or_cond = self._wait_conditions(step)
            if isinstance(det_or_cond, Detection):
                if self._apply_detection(det_or_cond, step, sr):
                    if self._step_satisfied(step):
                        return self._done(sr, "recovered", t0)
                    continue
                if self._step_satisfied(step):
                    return self._done(sr, "human", t0)
                continue
            if det_or_cond is not None:
                cond: WaitCondition = det_or_cond
                self._escalate_or_fail(
                    E.HardFailure(E.POSTCONDITION_FAILED, f"after '{step.description}', expected {cond.kind}={cond.value!r}",
                                  step_id=step.id, expected=f"{cond.kind}: {cond.value}", observed=self._observed(), escalate=True),
                    step, sr,
                )
                if self._step_satisfied(step):
                    return self._done(sr, "human", t0)
                continue

            det = self._detect(step)
            if det is not None:
                self._apply_detection(det, step, sr)
            return self._done(sr, "recovered" if sr.recoveries or self._interventions_for(step) else "ok", t0)

    def _verify_checkpoint(self, artifact: Artifact) -> None:
        cp = artifact.checkpoint
        self.evidence.log("checkpoint.begin", description=cp.description)
        for cond in cp.conditions:
            rendered = self._render_cond(cond)
            if not self.surface.wait_for(rendered):
                det = self._detect(None)
                if det is not None and det.outcome.kind == "business":
                    self._apply_detection(det, None, None)
                raise E.HardFailure(E.CHECKPOINT_FAILED, f"checkpoint '{cp.description}' not satisfied", step_id="checkpoint",
                                    expected=f"{rendered.kind}: {rendered.value}", observed=self._observed())
        for o in artifact.outputs:
            if o.name not in self._outputs_raw or self._outputs_raw[o.name] == "":
                raise E.HardFailure(E.OUTPUT_MISSING, f"declared output '{o.name}' was not extracted", step_id="checkpoint", expected=o.name, observed=str(self._outputs_raw))
        self._snap("checkpoint")
        self.evidence.log("checkpoint.ok", description=cp.description, outputs=self._outputs_raw)

    def _context(self, step: Step | None) -> DetectionContext:
        obs = self.surface.observe(screenshot=False)
        return DetectionContext(url=obs.url, text=obs.page_text, http_status=obs.http_status, overlays=obs.overlays, step_id=step.id if step else None)

    def _detect(self, step: Step | None) -> Detection | None:
        assert self._artifact is not None
        det = detect(self._artifact.outcomes, self._context(step))
        if det:
            self.evidence.log("outcome.detected", step=step.id if step else None, code=det.outcome.code, kind=det.outcome.kind, source=det.source, matched=det.matched)
        return det

    def _handle_detected(self, step: Step, sr: StepResult) -> bool:
        det = self._detect(step)
        if det is None:
            return False
        return self._apply_detection(det, step, sr)

    def _apply_detection(self, det: Detection, step: Step | None, sr: StepResult | None) -> bool:
        o = det.outcome
        step_id = step.id if step else None
        if o.kind == "business":
            raise E.BusinessOutcome(o.code, f"{o.description} - observed: \"{det.matched}\"", step_id=step_id, expected=(step.description if step else None), observed=det.matched)
        if o.kind == "recoverable":
            n = self._recovery_counts.get(o.code, 0) + 1
            self._recovery_counts[o.code] = n
            assert o.recovery is not None
            if n > o.recovery.max_attempts:
                failure = E.HardFailure(E.RECOVERY_EXHAUSTED, f"{o.code} recurred {n} times (max {o.recovery.max_attempts}); {o.description}",
                                        step_id=step_id, expected="condition resolved by recovery", observed=det.matched, escalate=o.escalate)
                self._escalate_or_fail(failure, step, sr)
                return True
            ok = self._recover(o.recovery, det)
            rec = {"code": o.code, "attempt": n, "recovery": o.recovery.action, "ok": ok, "matched": det.matched}
            if sr is not None:
                sr.recoveries.append(rec)
            self.evidence.log("recovery.applied", step=step_id, **rec)
            if not ok:
                self._escalate_or_fail(E.HardFailure(o.code, f"recovery '{o.recovery.action}' for {o.code} did not work: {o.description}",
                                                     step_id=step_id, expected=o.recovery.description or o.recovery.action, observed=det.matched, escalate=o.escalate), step, sr)
            return True
        self._escalate_or_fail(E.HardFailure(o.code, f"{o.description} - observed: \"{det.matched}\"", step_id=step_id,
                                             expected=(step.description if step else "clean page"), observed=det.matched, escalate=o.escalate), step, sr)
        return True

    def _recover(self, recovery: Recovery, det: Detection) -> bool:
        if recovery.action == "wait_retry":
            time.sleep(recovery.delay_ms / 1000)
            return True
        if recovery.action == "reload":
            time.sleep(recovery.delay_ms / 1000)
            return self.surface.goto(self.surface.current_url()).ok
        if recovery.action == "click":
            target = recovery.target
            if target is None:
                obs = self.surface.observe(screenshot=False)
                btn = next((e for e in obs.elements if e.in_overlay and e.role == "button" and _DISMISS_RX.match(e.name or e.text or "")), None)
                if btn is None:
                    return False
                target = Locator(description=f"overlay button '{btn.name}'", frame_path=btn.frame_path, candidates=btn.candidates, recorded={"role": btn.role, "name": btn.name})
            resolved, _ = resolve_locator(self.surface, target, self._params, allow_coords=self.allow_coords, passes=1)
            if resolved is None:
                return False
            res = self.surface.perform(Action(type="click", locator=target), resolved)
            time.sleep(0.3)
            return res.ok
        return False

    def _escalate_or_fail(self, failure: E.HardFailure, step: Step | None, sr: StepResult | None) -> None:
        if failure.escalate and self.escalation is not None:
            self._escalate(kind="stuck", step=step, reason_code=failure.code, reason=failure.message, suggested=["fix the screen manually, then resume", "abort"], failure=failure)
            return
        raise failure

    def _escalate(self, *, kind: str, step: Step | None, reason_code: str, reason: str, suggested: list[str], failure: E.HardFailure | None = None):
        assert self._artifact is not None
        if self.escalation is None:
            raise failure or E.EscalationAborted(reason_code, reason + " (no operator console configured)", step_id=step.id if step else None)
        shot = self._snap(f"handoff-before-{len(self._interventions) + 1:02d}")
        req = InterventionRequest(
            run_id=self.evidence.run_id, mode="replay", kind=kind, capability=f"{self._artifact.name} v{self._artifact.version}",
            step_ref=f"{step.id} - {step.description}" if step else "entry", reason_code=reason_code, reason=reason,
            url=self.surface.current_url(), screenshot=shot, session=self.surface.session_info(), suggested_actions=suggested,
        )
        resp = self.escalation.escalate(req)
        hi = HumanIntervention(step_id=step.id if step else None, kind=kind, reason_code=reason_code, reason=reason, operator=resp.operator,
                               decision=resp.decision, human_actions=resp.human_actions, took_s=resp.took_s)
        self._interventions.append(hi)
        if resp.decision != "resume":
            if resp.operator == "none":
                msg = f"needs a human ({reason_code}) but this run is unattended (operator=none); intervention request was raised and recorded. {reason}"
            else:
                msg = f"operator '{resp.operator}' aborted at {reason_code}: {reason}"
            raise E.EscalationAborted(reason_code, msg, step_id=step.id if step else None,
                                      expected=failure.expected if failure else None, observed=failure.observed if failure else None)
        return resp

    def _action_for(self, step: Step) -> Action:
        opts = {k: (Artifact.render(v, self._params) if isinstance(v, str) else v) for k, v in step.options.items()}
        if step.action == "extract":
            opts["output_name"] = step.output
        return Action(type=step.action, locator=step.target, text=Artifact.render(step.input, self._params), options=opts, reason=step.description)

    def _render_cond(self, cond: WaitCondition) -> WaitCondition:
        if isinstance(cond.value, str):
            return cond.model_copy(update={"value": Artifact.render(cond.value, self._params)})
        return cond

    def _wait_conditions(self, step: Step) -> Detection | WaitCondition | None:
        if not step.expect:
            return None
        conds = [self._render_cond(c) for c in step.expect]
        deadline = time.time() + max(c.timeout_ms for c in conds) / 1000
        pending = list(conds)
        while True:
            pending = [c for c in pending if not self.surface.wait_for(c.model_copy(update={"timeout_ms": 250}))]
            if not pending:
                return None
            det = self._detect(step)
            if det is not None:
                return det
            if time.time() >= deadline:
                return pending[0]
            time.sleep(0.3)

    def _step_satisfied(self, step: Step) -> bool:
        if not step.expect:
            return False
        return all(self.surface.wait_for(self._render_cond(c).model_copy(update={"timeout_ms": 1500})) for c in step.expect)

    def _done(self, sr: StepResult, status: str, t0: float) -> StepResult:
        sr.status = status
        sr.duration_ms = int((time.time() - t0) * 1000)
        self.evidence.log("step.done", step=sr.step_id, status=status, locator=sr.locator_used, attempts=sr.attempts, ms=sr.duration_ms)
        return sr

    def _interventions_for(self, step: Step) -> list[HumanIntervention]:
        return [h for h in self._interventions if h.step_id == step.id]

    def _observed(self) -> str:
        text = self.surface.page_text()
        return f"url={self.surface.current_url()} status={self.surface.last_http_status()} text=\"{text[:160]}\""

    @staticmethod
    def _expected_target(loc: Locator) -> str:
        return f"{loc.description}; candidates: " + ", ".join(f"{c.strategy}({', '.join(f'{k}={v}' for k, v in c.value.items())})" for c in loc.candidates)

    def _snap(self, name: str) -> str | None:
        self._shot += 1
        try:
            return self.evidence.screenshot(f"{self._shot:02d}-{name}", self.surface.screenshot())
        except Exception:
            return None

    def _typed_outputs(self, artifact: Artifact) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for o in artifact.outputs:
            raw = self._outputs_raw.get(o.name, "")
            try:
                if o.type == "currency":
                    neg = raw.strip().startswith(("-", "(")) or raw.strip().endswith("-")
                    num = float(re.sub(r"[^\d.]", "", raw) or "nan")
                    out[o.name] = -num if neg else num
                elif o.type == "integer":
                    out[o.name] = int(re.sub(r"[^\d-]", "", raw))
                elif o.type == "number":
                    out[o.name] = float(re.sub(r"[^\d.\-]", "", raw))
                elif o.type == "boolean":
                    out[o.name] = raw.strip().lower() in ("true", "yes", "y", "1", "active", "open")
                else:
                    out[o.name] = raw
            except ValueError:
                out[o.name] = raw
        return out

    def _finish(self, result: ReplayResult, t0: float) -> ReplayResult:
        result.duration_ms = int((time.time() - t0) * 1000)
        self._snap("final")
        self.evidence.write_json("result.json", result.model_dump(mode="json"))
        self.evidence.finalize({"kind": "replay", "status": result.status.value, "outcome_code": result.outcome_code, "artifact": result.artifact_name, "outputs": result.outputs})
        self.evidence.log("replay.end", status=result.status.value, outcome_code=result.outcome_code, message=result.message, ms=result.duration_ms)
        return result
