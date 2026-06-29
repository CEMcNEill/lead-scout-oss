"""person_research — Clay/LinkedIn enrichment, then synthesis.

Assesses seniority, role, budget ownership, and likely pain for one person.
For product-led leads the qualifier calls this across the usage roster to do
buying-group discovery, so enrich() takes a person_ref and is meant to be looped.
"""

from __future__ import annotations

from typing import Any, Callable

from shared.model import ModelClient
from shared.tools.base import ToolResult, run_synthesis
from shared.tools.fetchers import PersonFetcher

_FIELDS = ["seniority", "role", "budget_ownership", "likely_pain"]


class PersonResearchTool:
    def __init__(
        self, fetcher: PersonFetcher, model: ModelClient, next_id: Callable[[], str]
    ) -> None:
        self._fetcher = fetcher
        self._model = model
        self._next_id = next_id

    def enrich(self, person_ref: dict[str, Any]) -> ToolResult:
        raw = self._fetcher.enrich(person_ref)
        label = person_ref.get("email") or person_ref.get("name") or "person"
        return run_synthesis(
            self._model,
            step=f"person_research.synthesis:{label}",
            fields_wanted=_FIELDS,
            fetcher_output=raw,
            source="person_research",
            next_id=self._next_id,
            extra="This is one person. Assess only what the enrichment supports.",
        )
