"""crm_context — the Salesforce read. Ground truth, deterministic, no model.

Returns the raw CRM record (which the shell's hard-stop check reads) and a set
of ground-truth Claims built directly from the record's fields. These Claims are
grounded trivially: their raw IS the field the read returned. For inbound leads
the record carries the inbound message text.
"""

from __future__ import annotations

from typing import Any, Callable

from shared.tools.base import ToolResult
from shared.tools.fetchers import CrmFetcher
from shared.tools.grounding import ground_candidates

# (claim field name, dotted path into the CRM record)
_GROUND_TRUTH_FIELDS: list[tuple[str, str]] = [
    ("contact_name", "lead.name"),
    ("contact_email", "lead.email"),
    ("contact_title", "lead.title"),
    ("company_name", "lead.company"),
    ("company_domain", "lead.domain"),
    ("lead_source", "lead.lead_source"),
    ("inbound_message", "inbound_message"),
]


class CrmContextTool:
    def __init__(self, fetcher: CrmFetcher, next_id: Callable[[], str]) -> None:
        self._fetcher = fetcher
        self._next_id = next_id

    def read(self, task_id: str) -> ToolResult:
        record = self._fetcher.read(task_id)
        candidates: list[dict[str, Any]] = []
        for field_name, path in _GROUND_TRUTH_FIELDS:
            value = _dig(record, path)
            if value is not None and value != "":
                candidates.append(
                    {"field": field_name, "value": value, "raw_keys": [path], "confidence": 1.0}
                )
        claims, rejected = ground_candidates(
            candidates, record, source="crm_context", next_id=self._next_id
        )
        return ToolResult(claims=claims, raw=record, rejected=rejected)


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for seg in path.split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return None
    return cur
