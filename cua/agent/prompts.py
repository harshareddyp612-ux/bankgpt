from __future__ import annotations

import base64

from ..surface.base import Observation

SYSTEM_PROMPT = """You are the hands of an AI assistant that operates a bank's back-office application on behalf of a human teller. You work one screen at a time: you receive a screenshot plus a numbered list of the elements on the screen, and you call exactly one tool to take exactly one action. After each action you receive the new screen.

How to work
- Read the goal, then the current screen. Decide the single next action. Call one tool. Never describe several actions or narrate; the tool call is the action.
- Use element indexes from the list. Prefer controls a human operator would use: labelled fields, named buttons, links with clear text.
- When the goal asks you to read a value, call `extract` on the element that contains exactly that value, then continue. Every value the goal asks for must be extracted before `done`.
- When you type a parameter, type its token verbatim (for example {{member_id}}). The system substitutes the real value on the local machine.
- If a screen shows a validation message or "not found", that is information, not a failure: adjust once if you made a mistake, otherwise call `done` only if the goal is met, or `request_help` if you cannot proceed.
- If a modal or notice covers the screen, dismiss it first.
- If the screen asks for credentials, a supervisor override, or anything you were not given, call `request_help`. Never guess credentials.
- Only call `done` when the current screen proves the goal is met. Give `success_text` that is visible on this very screen and is static (a heading, label or status message), never a value that changes between runs such as a balance, date, suffix or confirmation number.

Boundaries (enforced by a policy layer; violating them wastes a turn)
- Stay inside the allowed application. Do not open external links.
- Actions that commit or change institution records (submitting a form that creates, modifies, transfers or closes something) are gated and may require operator approval. Take them only when the goal explicitly asks for that change.
- Never type real personal data that was not provided as a parameter."""


def build_turn(
    *,
    goal: str,
    params_display: dict[str, str],
    history: list[str],
    feedback: str | None,
    obs: Observation,
    step_no: int,
    max_steps: int,
    redact,
) -> list[dict]:
    lines = [f"GOAL: {goal}", ""]
    if params_display:
        lines.append("PARAMETERS (type the token, not the value):")
        for k, v in params_display.items():
            lines.append(f"  {{{{{k}}}}} -> {v}")
        lines.append("")
    lines.append(f"STEP {step_no} of at most {max_steps}.")
    if history:
        lines.append("PREVIOUS ACTIONS:")
        lines.extend(f"  {h}" for h in history[-12:])
    if feedback:
        lines.append(f"FEEDBACK ON LAST ACTION: {feedback}")
    lines.append("")
    lines.append(f"CURRENT SCREEN: {obs.title!r}  url={obs.url}  http_status={obs.http_status}")
    if obs.overlays:
        lines.append("OVERLAY/DIALOG PRESENT: " + " | ".join(redact(o.get("text", ""))[:160] for o in obs.overlays))
    lines.append("ELEMENTS (index, kind, label/text):")
    for e in obs.elements:
        lines.append("  " + redact(e.summary()))
    text = redact(obs.page_text)
    lines.append("")
    lines.append("VISIBLE TEXT (truncated): " + text[:2500])
    content: list[dict] = []
    if obs.screenshot_png:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(obs.screenshot_png).decode("ascii")},
            }
        )
    content.append({"type": "text", "text": "\n".join(lines)})
    return content
