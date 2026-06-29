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


# --- deterministic fake --------------------------------------------------

ScriptedResponse = str | Callable[[str, str], str]


class FakeModel:
    """Scripted ModelClient for tests and fixture runs.

    Map a `step` to either a fixed response string or a callable
    (system, prompt) -> str. Every call is recorded for assertions. Token counts
    are derived deterministically from text length so cost tests are stable.
    """

    def __init__(self, script: dict[str, ScriptedResponse] | None = None,
                 model: str = "fake-model") -> None:
        self.script: dict[str, ScriptedResponse] = dict(script or {})
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def set(self, step: str, response: ScriptedResponse) -> None:
        self.script[step] = response

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
