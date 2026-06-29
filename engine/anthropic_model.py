"""The real Claude-backed ModelClient.

Maps a call's tier to a concrete model via the model policy and calls the
Anthropic API. Tests never touch this; they inject FakeModel. Wrap an instance
in MeteredModel (via the budget governor) so its spend is recorded.
"""

from __future__ import annotations

import os

from engine.cost import ModelPolicy
from shared.model import ModelResponse


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
