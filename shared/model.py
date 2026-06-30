"""The model interface every Claude call goes through.

Defined as a narrow Protocol so two things are possible: the cost layer can wrap
it as middleware to meter spend (task: cost), and tests can inject a scripted
fake for full determinism. Synthesis tools, qualifiers, the drafter, and the
fact-check gate all call a ModelClient rather than the Anthropic SDK directly.

The real Anthropic-backed client is wired in the cost layer alongside the model
policy that maps each call's `tier` to a concrete model. This module only
defines the contract and a deterministic fake.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass
class ModelResponse:
    text: str
    model: str
    tokens_in: int
    tokens_out: int


class ModelTier(str):
    """Stakes-based tiers from the model policy. String values so they serialize
    trivially and a policy can map them to concrete model ids."""

    ROUTING_FALLBACK = "routing_fallback"
    RESEARCH_SYNTHESIS = "research_synthesis"
    QUALIFIER_JUDGMENT = "qualifier_judgment"
    DRAFTER = "drafter"
    LEARNING = "learning"
    # The agentic qualifier loop's "which tool next" decision. Cheaper than the
    # authoritative judge/draft so the loop adds no Opus spend (see cost policy).
    AGENT_ORCHESTRATION = "agent_orchestration"


# --- tool-use (agentic loop) ---------------------------------------------


@dataclass
class ToolSpec:
    """A tool offered to the model in an agentic turn: name + description +
    JSON-schema for its input."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str          # provider tool_use id, echoed back in the matching tool_result
    name: str        # the ToolSpec.name the model chose
    input: dict[str, Any]


@dataclass
class ToolTurn:
    """One model turn in an agentic loop. `assistant_content` is the provider's
    raw assistant blocks, replayed verbatim on the next turn (thinking blocks must
    echo unchanged on the same model); the qualifier owns the message list."""

    assistant_content: Any
    tool_calls: list[ToolCall]   # empty => the model is done (stop_reason end_turn)
    stop_reason: str             # "tool_use" | "end_turn" | "max_tokens" | "refusal"
    model: str
    tokens_in: int
    tokens_out: int
    text: str | None = None      # assistant prose this turn (final answer when done)


class ModelClient(Protocol):
    """A single completion call. `tier` selects model by stakes; `step` names the
    call site so cost can be attributed and the fake can be scripted."""

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        tier: str,
        step: str,
        max_tokens: int = 2048,
    ) -> ModelResponse: ...

    def run_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        tier: str,
        step: str,
        max_tokens: int = 4096,
        tool_choice: dict[str, Any] | None = None,
    ) -> ToolTurn:
        """One tool-use turn. The caller owns the running `messages` list (the
        Anthropic conversation: user/assistant turns with tool_result blocks). Each
        call is one metered model turn, so MeteredModel's per-run cap bounds the
        loop mid-flight."""
        ...


# --- deterministic fake --------------------------------------------------

ScriptedResponse = str | Callable[[str, str], str]


class FakeModel:
    """Scripted ModelClient for tests and fixture runs.

    Map a `step` to either a fixed response string or a callable
    (system, prompt) -> str. Every call is recorded for assertions. Token counts
    are derived deterministically from text length so cost tests are stable.
    """

    def __init__(self, script: dict[str, ScriptedResponse] | None = None,
                 model: str = "fake-model",
                 tool_script: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.script: dict[str, ScriptedResponse] = dict(script or {})
        # step -> ordered list of turns; each turn is {"tool_calls": [{name, input}]}
        # or {"text": str, "stop": "end_turn"}. Consumed by a per-step cursor.
        self.tool_script: dict[str, list[dict[str, Any]]] = {
            k: list(v) for k, v in (tool_script or {}).items()
        }
        self._tool_cursor: dict[str, int] = {}
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def set(self, step: str, response: ScriptedResponse) -> None:
        self.script[step] = response

    def set_tools(self, step: str, turns: list[dict[str, Any]]) -> None:
        self.tool_script[step] = list(turns)

    def run_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list["ToolSpec"],
        tier: str,
        step: str,
        max_tokens: int = 4096,
        tool_choice: dict[str, Any] | None = None,
    ) -> "ToolTurn":
        self.calls.append({"step": step, "tier": tier, "kind": "run_turn",
                           "system": system, "messages": list(messages),
                           "tools": [t.name for t in tools]})
        if step not in self.tool_script:
            raise KeyError(
                f"FakeModel has no tool script for step {step!r}; "
                f"scripted tool steps: {sorted(self.tool_script)}"
            )
        turns = self.tool_script[step]
        i = self._tool_cursor.get(step, 0)
        if i >= len(turns):
            raise KeyError(f"FakeModel tool script for step {step!r} exhausted "
                           f"after {len(turns)} turn(s)")
        self._tool_cursor[step] = i + 1
        turn = turns[i]
        raw = turn.get("tool_calls", [])
        tool_calls = [
            ToolCall(id=f"{step}-{i}-{j}", name=c["name"], input=c.get("input", {}))
            for j, c in enumerate(raw)
        ]
        text = turn.get("text")
        stop = turn.get("stop", "tool_use" if tool_calls else "end_turn")
        return ToolTurn(
            assistant_content=turn.get("assistant_content", {"_fake": step, "_turn": i}),
            tool_calls=tool_calls,
            stop_reason=stop,
            model=self.model,
            tokens_in=max(1, len(str(messages)) // 4),
            tokens_out=max(1, (len(text) if text else 0) // 4 + len(raw)),
            text=text,
        )

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        tier: str,
        step: str,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        self.calls.append({"step": step, "tier": tier, "system": system, "prompt": prompt})
        if step not in self.script:
            raise KeyError(
                f"FakeModel has no scripted response for step {step!r}; "
                f"scripted steps: {sorted(self.script)}"
            )
        scripted = self.script[step]
        text = scripted(system, prompt) if callable(scripted) else scripted
        return ModelResponse(
            text=text,
            model=self.model,
            tokens_in=max(1, len(prompt) // 4),
            tokens_out=max(1, len(text) // 4),
        )


# --- helpers -------------------------------------------------------------


def parse_json(text: str) -> Any:
    """Parse a JSON object/array from model text, tolerating ```json fences and
    surrounding prose. Raises ValueError if nothing parses."""
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # last resort: grab the first {...} or [...] span
    match = re.search(r"(\{.*\}|\[.*\])", stripped, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    raise ValueError(f"no JSON found in model output: {text[:200]!r}")
