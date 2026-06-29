"""Creation-time grounding: the fact-check checkpoint where Claims are born.

The synthesis layer proposes candidate assessments, each citing the fetcher
output keys it rests on. This module binds each candidate to the *actual* values
the fetcher returned and refuses to mint a Claim whose cited keys are not present
in that output. A candidate the model hallucinated against data that was never
fetched is dropped here, before it can enter a dossier.

This is the spec's rule made structural: "Synthesis may only emit a Claim whose
`raw` traces to fetcher output."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from shared.contracts import Claim


class IdAllocator:
    """Hands out run-unique Claim ids. One per run, so ids are stable and a
    Disposition can reference any Claim regardless of which tool produced it."""

    def __init__(self, prefix: str = "c") -> None:
        self.prefix = prefix
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"{self.prefix}{self.n}"


def resolve_path(obj: Any, path: str) -> tuple[bool, Any]:
    """Resolve a dotted path (e.g. "roster.0.email") against nested dicts/lists.

    Returns (found, value). `found` is False if any segment is missing, so a
    present-but-null value is distinguishable from an absent one.
    """
    cur = obj
    for seg in path.split("."):
        if isinstance(cur, dict):
            if seg not in cur:
                return False, None
            cur = cur[seg]
        elif isinstance(cur, list):
            try:
                idx = int(seg)
            except ValueError:
                return False, None
            if idx < 0 or idx >= len(cur):
                return False, None
            cur = cur[idx]
        else:
            return False, None
    return True, cur


@dataclass
class RejectedCandidate:
    field: str
    missing_keys: list[str]


def ground_candidates(
    candidates: list[dict[str, Any]],
    fetcher_output: Any,
    *,
    source: str,
    next_id: Callable[[], str],
) -> tuple[list[Claim], list[RejectedCandidate]]:
    """Turn synthesis candidates into grounded Claims.

    Each candidate is a dict: {field, value, raw_keys: [paths], confidence}.
    Every cited key must resolve against `fetcher_output`; otherwise the whole
    candidate is rejected. The resulting Claim's `raw` is a {path: value} map of
    the real fetched values, so provenance is exact and auditable. `next_id`
    yields run-unique ids (see IdAllocator).
    """
    claims: list[Claim] = []
    rejected: list[RejectedCandidate] = []
    for cand in candidates:
        raw_keys = cand.get("raw_keys", [])
        if not raw_keys:
            rejected.append(
                RejectedCandidate(field=cand.get("field", "?"), missing_keys=["<none cited>"])
            )
            continue
        resolved: dict[str, Any] = {}
        missing: list[str] = []
        for key in raw_keys:
            found, value = resolve_path(fetcher_output, key)
            if found:
                resolved[key] = value
            else:
                missing.append(key)
        if missing:
            rejected.append(RejectedCandidate(field=cand.get("field", "?"), missing_keys=missing))
            continue
        claims.append(
            Claim(
                id=next_id(),
                field=cand["field"],
                value=cand["value"],
                source=source,
                raw=resolved,
                confidence=float(cand.get("confidence", 0.5)),
            )
        )
    return claims, rejected
