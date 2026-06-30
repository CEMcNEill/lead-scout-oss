"""The real Claude-backed ModelClient.

Maps a call's tier to a concrete model via the model policy and calls the
Anthropic API. Tests never touch this; they inject FakeModel. Wrap an instance
in MeteredModel (via the budget governor) so its spend is recorded.
"""

from __future__ import annotations

import os

from engine.cost import ModelPolicy
from shared.model import ModelResponse, ToolCall, ToolSpec, ToolTurn


class AnthropicModel:
    def __init__(self, policy: ModelPolicy, api_key: str | None = None) -> None:
        self._policy = policy
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None  # lazily created so importing this module needs no key

    def _ensure_client(self):
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set; cannot make live model calls"
                )
            from anthropic import Anthropic

            self._client = Anthropic(api_key=self._api_key)
        return self._client

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        tier: str,
        step: str,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        model = self._policy.model_for(tier)
        client = self._ensure_client()
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return ModelResponse(
            text=text,
            model=model,
            tokens_in=resp.usage.input_tokens,
            tokens_out=resp.usage.output_tokens,
        )

    def run_turn(
        self,
        *,
        system: str,
        messages: list,
        tools: list[ToolSpec],
        tier: str,
        step: str,
        max_tokens: int = 4096,
        tool_choice: dict | None = None,
    ) -> ToolTurn:
        model = self._policy.model_for(tier)
        client = self._ensure_client()
        kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=[
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
            # adaptive thinking + effort; never temperature/top_p/budget_tokens (400 on Opus 4.8).
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
        )
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        resp = client.messages.create(**kwargs)
        text_parts = [b.text for b in resp.content if b.type == "text"]
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in resp.content
            if b.type == "tool_use"
        ]
        return ToolTurn(
            # resp.content is replayed verbatim as the next assistant turn; thinking
            # blocks must echo unchanged on the same model.
            assistant_content=resp.content,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason,
            model=model,
            tokens_in=resp.usage.input_tokens,
            tokens_out=resp.usage.output_tokens,
            text="".join(text_parts) or None,
        )
