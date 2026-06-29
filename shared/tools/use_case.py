"""use_case_mapping — the PostHog use-case-selling check.

A grounded judgment about the probable use case(s) and the PostHog product(s)
they map to. For inbound it reasons from the message plus the persona; for an
existing account, from the usage patterns. Emits use-case Claims into the
dossier so the disposition can reference them and the drafter can lead with the
use case and the pain it solves.

Evidence sources are passed as a dict (e.g. {"message": ..., "usage": ...,
"persona": ...}); each emitted use case must cite raw_keys that exist within it,
which is the same creation-time grounding rule the other tools obey.
"""

from __future__ import annotations

from typing import Any, Callable

from shared.contracts import Product
from shared.model import ModelClient, ModelTier, parse_json
from shared.tools.base import ToolResult
from shared.tools.grounding import RejectedCandidate, ground_candidates

_SYSTEM = (
    "You map evidence to PostHog use cases. You are given evidence sources as "
    "JSON. Return a JSON array; each element is "
    '{"use_case": str, "product": one of '
    "[analytics, replay, flags, experiments, surveys, data_warehouse, "
    "llm_analytics, error_tracking, web_analytics], "
    '"owner_persona": str|null, "raw_keys": [<dotted paths into the evidence '
    'that ground this>], "confidence": 0..1}. Only emit a use case the evidence '
    "supports. Every raw_key must exist in the evidence. Do not invent use cases."
)


class UseCaseMappingTool:
    def __init__(self, model: ModelClient, next_id: Callable[[], str]) -> None:
        self._model = model
        self._next_id = next_id

    def map(self, evidence_sources: dict[str, Any]) -> ToolResult:
        import json

        resp = self._model.complete(
            system=_SYSTEM,
            prompt=f"Evidence sources:\n```json\n{json.dumps(evidence_sources, indent=2)}\n```",
            tier=ModelTier.RESEARCH_SYNTHESIS,
            step="use_case_mapping.synthesis",
        )
        parsed = parse_json(resp.text)
        raw_candidates = parsed if isinstance(parsed, list) else parsed.get("use_cases", [])

        # validate product, then reshape into the {field,value,raw_keys} form
        # ground_candidates expects, so grounding stays uniform across tools.
        candidates: list[dict[str, Any]] = []
        rejected_products: list[RejectedCandidate] = []
        for uc in raw_candidates:
            product = uc.get("product")
            if product not in Product._value2member_map_:
                rejected_products.append(
                    RejectedCandidate(field="use_case", missing_keys=[f"bad product: {product!r}"])
                )
                continue
            candidates.append(
                {
                    "field": "use_case",
                    "value": {
                        "use_case": uc.get("use_case"),
                        "product": product,
                        "owner_persona": uc.get("owner_persona"),
                    },
                    "raw_keys": uc.get("raw_keys", []),
                    "confidence": uc.get("confidence", 0.5),
                }
            )
        claims, rejected = ground_candidates(
            candidates, evidence_sources, source="use_case_mapping", next_id=self._next_id
        )
        return ToolResult(claims=claims, raw=evidence_sources, rejected=rejected + rejected_products)
