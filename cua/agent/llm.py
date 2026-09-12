from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..surface.base import Observation

DEFAULT_MODEL = "claude-opus-5"


class LLMForbidden(RuntimeError):
    pass


class LLMRefusal(RuntimeError):
    pass


def _guard() -> None:
    if os.environ.get("CUA_NO_LLM") == "1":
        raise LLMForbidden("LLM use is forbidden on this path (CUA_NO_LLM=1): replay must be deterministic")


@dataclass
class TurnContext:
    goal: str
    params: dict[str, str]
    params_display: dict[str, str]
    history: list[str]
    history_structured: list[dict[str, Any]]
    feedback: str | None
    obs: Observation
    step_no: int
    max_steps: int
    blocks: list[dict] = field(default_factory=list)


@dataclass
class Decision:
    tool: str | None
    args: dict[str, Any]
    text: str = ""
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    raw_stop_reason: str | None = None


class LLMClient(Protocol):
    name: str

    def decide(self, system: str, ctx: TurnContext, tools: list[dict]) -> Decision: ...


class AnthropicLLM:
    def __init__(self, model: str | None = None, effort: str | None = None, max_tokens: int = 4096):
        _guard()
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("CUA_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.environ.get("CUA_EFFORT", "medium")
        self.max_tokens = max_tokens
        self.name = f"anthropic:{self.model}"
        self.calls = 0

    def decide(self, system: str, ctx: TurnContext, tools: list[dict]) -> Decision:
        _guard()
        self.calls += 1
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=tools,
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                messages=[{"role": "user", "content": ctx.blocks}],
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except self._anthropic.RateLimitError as e:
            raise RuntimeError(f"rate limited by the API: {e.message}") from e
        except self._anthropic.AuthenticationError as e:
            raise RuntimeError("Anthropic authentication failed - set ANTHROPIC_API_KEY (see .env.example)") from e
        except self._anthropic.APIStatusError as e:
            raise RuntimeError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise RuntimeError(f"could not reach the Anthropic API: {e}") from e

        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise LLMRefusal(f"model declined to act (category={cat})")
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        text = " ".join(b.text for b in resp.content if b.type == "text").strip()
        tool_block = next((b for b in resp.content if b.type == "tool_use"), None)
        if tool_block is None:
            return Decision(tool=None, args={}, text=text, model=resp.model, usage=usage, raw_stop_reason=resp.stop_reason)
        return Decision(tool=tool_block.name, args=dict(tool_block.input), text=text, model=resp.model, usage=usage, raw_stop_reason=resp.stop_reason)
