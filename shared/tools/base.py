"""Shared plumbing for the toolbox primitives.

Every synthesis tool follows the same two-layer shape: call a deterministic
fetcher, then ask the model for assessments that cite the fetcher's output keys,
then ground those into Claims. `run_synthesis` captures that shape so each tool
file stays about its prompt, not its mechanics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from shared.contracts import Claim
from shared.model import ModelClient, ModelTier, parse_json
from shared.tools.grounding import IdAllocator, RejectedCandidate, ground_candidates


@dataclass
class ToolResult:
    """What a synthesis tool returns: grounded Claims, the raw fetcher output
    they rest on, and any candidates that failed grounding (dropped, never
    asserted)."""

    claims: list[Claim]
    raw: Any
    rejected: list[RejectedCandidate] = field(default_factory=list)


_SYNTHESIS_SYSTEM = (
    "You are the synthesis layer of a grounded research tool. You are given the "
    "raw output of a deterministic fetcher as JSON. Produce assessments ONLY for "
    "what the raw output supports. Return a JSON array; each element is "
    '{"field": str, "value": <any>, "raw_keys": [<dotted paths into the raw '
    'output that justify this>], "confidence": 0..1}. Every raw_key MUST be a '
    "path that exists in the provided raw output. If the raw output does not "
    "support an assessment, do not emit it. Do not invent facts."
)


def _synthesis_prompt(fields_wanted: list[str], fetcher_output: Any, extra: str = "") -> str:
    return (
        f"Fields to assess where supported: {', '.join(fields_wanted)}.\n\n"
        f"{extra}\n\n"
        f"Raw fetcher output:\n```json\n{json.dumps(fetcher_output, indent=2)}\n```"
    )


def run_synthesis(
    model: ModelClient,
    *,
    step: str,
    fields_wanted: list[str],
    fetcher_output: Any,
    source: str,
    next_id,
    extra: str = "",
    tier: str = ModelTier.RESEARCH_SYNTHESIS,
) -> ToolResult:
    """Drive one synthesis call and ground its output into Claims."""
    resp = model.complete(
        system=_SYNTHESIS_SYSTEM,
        prompt=_synthesis_prompt(fields_wanted, fetcher_output, extra),
        tier=tier,
        step=step,
    )
    parsed = parse_json(resp.text)
    candidates = parsed if isinstance(parsed, list) else parsed.get("claims", [])
    claims, rejected = ground_candidates(
        candidates, fetcher_output, source=source, next_id=next_id
    )
    return ToolResult(claims=claims, raw=fetcher_output, rejected=rejected)


__all__ = ["ToolResult", "run_synthesis", "IdAllocator"]
