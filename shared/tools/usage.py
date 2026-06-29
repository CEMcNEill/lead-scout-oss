"""usage_research — the PostHog usage query, then synthesis.

Assesses event volume, products touched, trajectory, and activation/expansion
signals. For product-led leads the raw output also carries the internal user
roster (who in the account is on PostHog and what each touches); the qualifier
reads result.raw["roster"] to drive buying-group discovery.
"""

from __future__ import annotations

from typing import Any, Callable

from shared.contracts import Claim
from shared.model import ModelClient
from shared.tools.base import ToolResult, run_synthesis
from shared.tools.fetchers import UsageFetcher

# field name for the deterministic account-resolution Claim (read by the Slack
# notifier and the slow loop)
USAGE_RESOLUTION_FIELD = "usage_account_resolution"

_FIELDS = [
    "monthly_event_volume",
    "products_touched",
    "trajectory",
    "activation_signals",
    "expansion_signals",
    "plan_and_billing",  # free/PAYG/paid, MRR, invoices -> big-fish-on-free / rolloff
]


def _account_label(ref: Any) -> str:
    """Stable label for the synthesis step, regardless of whether the qualifier
    passed an account id or the whole CRM record (for account resolution)."""
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict):
        return ref.get("account_ref") or "account"
    return "account"


class UsageResearchTool:
    def __init__(
        self, fetcher: UsageFetcher, model: ModelClient, next_id: Callable[[], str]
    ) -> None:
        self._fetcher = fetcher
        self._model = model
        self._next_id = next_id

    def query(self, ref: Any) -> ToolResult:
        raw = self._fetcher.query(ref)
        result = run_synthesis(
            self._model,
            step=f"usage_research.synthesis:{_account_label(ref)}",
            fields_wanted=_FIELDS,
            fetcher_output=raw,
            source="usage_research",
            next_id=self._next_id,
            extra=(
                "The raw output may include account resolution provenance and a "
                "'roster' of internal users. Assess account-level usage, plan, and "
                "billing; do not assert per-person facts here."
            ),
        )
        self._maybe_add_resolution_claim(raw, result)
        return result

    def _maybe_add_resolution_claim(self, raw: Any, result: ToolResult) -> None:
        """When account resolution had more than one candidate, record it as a
        deterministic, grounded Claim so the Slack card can surface the choices
        for the rep to confirm and the slow loop can act on the reply."""
        resolution = raw.get("resolution") if isinstance(raw, dict) else None
        if not resolution or len(resolution.get("candidates", [])) <= 1:
            return
        result.claims.append(
            Claim(
                id=self._next_id(),
                field=USAGE_RESOLUTION_FIELD,
                value={
                    "chosen": resolution.get("chosen_account_id"),
                    "ambiguous": resolution.get("ambiguous"),
                    "candidates": resolution.get("candidates"),
                },
                source="usage_research",
                raw={"resolution": resolution},
                confidence=1.0,
            )
        )

    @staticmethod
    def roster(result: ToolResult) -> list[dict[str, Any]]:
        """Convenience accessor for the internal user roster, for buying-group
        discovery."""
        raw = result.raw or {}
        return raw.get("roster", []) if isinstance(raw, dict) else []
