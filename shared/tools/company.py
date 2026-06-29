"""company_research — firmographic + technographic enrichment, then synthesis.

Assesses ICP fit, segment, buying signals, and tech stack for one company.
"""

from __future__ import annotations

from typing import Callable

from shared.model import ModelClient
from shared.tools.base import ToolResult, run_synthesis
from shared.tools.fetchers import CompanyFetcher

_FIELDS = ["icp_industry_fit", "segment", "buying_signals", "tech_stack"]


class CompanyResearchTool:
    def __init__(
        self, fetcher: CompanyFetcher, model: ModelClient, next_id: Callable[[], str]
    ) -> None:
        self._fetcher = fetcher
        self._model = model
        self._next_id = next_id

    def enrich(self, domain: str) -> ToolResult:
        raw = self._fetcher.enrich(domain)
        return run_synthesis(
            self._model,
            step=f"company_research.synthesis:{domain}",
            fields_wanted=_FIELDS,
            fetcher_output=raw,
            source="company_research",
            next_id=self._next_id,
        )
