"""The boundary fact-check gate: grounding enforced at use.

Before any draft is staged, the shell runs a verification pass over every
factual assertion in the returned draft and confirms each maps to a grounded
Claim in the returned dossier. Anything unsupported is flagged, never staged
clean. This gate runs regardless of how the qualifier produced the draft, so an
ungrounded claim cannot reach the rep even if a qualifier is sloppy.

Two steps, deliberately separated:
  1. Extraction (model): pull the factual assertions out of the draft and
     attribute each to a dossier claim id, or null if nothing supports it.
  2. Verification (deterministic): a cited claim id must actually exist in the
     dossier. This step is pure and fully testable; it is the invariant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from shared.contracts import Claim, Draft
from shared.model import ModelClient, ModelTier, parse_json

_SYSTEM = (
    "You verify that a draft email asserts no fact the evidence does not support. "
    "You are given the draft and a list of grounded Claims (id, field, value). "
    "Extract every FACTUAL assertion the draft makes about the recipient, their "
    "company, or their product usage. Ignore pleasantries, opinions, questions, "
    "and calls to action. For each assertion, attribute it to the single Claim id "
    "that grounds it, or null if no Claim does. Return JSON: "
    '[{"assertion": str, "claim_ref": <claim id or null>}].'
)


@dataclass
class CheckedAssertion:
    text: str
    claim_ref: str | None
    grounded: bool


@dataclass
class FactCheckResult:
    assertions: list[CheckedAssertion]

    @property
    def passed(self) -> bool:
        """True iff every factual assertion maps to a real grounded Claim."""
        return all(a.grounded for a in self.assertions)

    @property
    def ungrounded(self) -> list[CheckedAssertion]:
        return [a for a in self.assertions if not a.grounded]


def verify_assertions(
    extracted: list[dict], dossier_ids: set[str]
) -> list[CheckedAssertion]:
    """Deterministic verification: a claim_ref grounds an assertion only if it is
    non-null and names a Claim actually present in the dossier."""
    checked: list[CheckedAssertion] = []
    for item in extracted:
        ref = item.get("claim_ref")
        grounded = ref is not None and ref in dossier_ids
        checked.append(
            CheckedAssertion(text=item.get("assertion", ""), claim_ref=ref, grounded=grounded)
        )
    return checked


def factcheck(draft: Draft, dossier: list[Claim], model: ModelClient) -> FactCheckResult:
    """Run the boundary gate over a draft against its dossier."""
    dossier_ids = {c.id for c in dossier}
    claims_view = [
        {"id": c.id, "field": c.field, "value": c.value} for c in dossier
    ]
    prompt = (
        f"Draft subject: {draft.subject}\n\n"
        f"Draft body:\n{draft.body}\n\n"
        f"Grounded Claims:\n```json\n{json.dumps(claims_view, indent=2)}\n```"
    )
    resp = model.complete(
        system=_SYSTEM, prompt=prompt, tier=ModelTier.RESEARCH_SYNTHESIS, step="factcheck"
    )
    parsed = parse_json(resp.text)
    extracted = parsed if isinstance(parsed, list) else parsed.get("assertions", [])
    return FactCheckResult(assertions=verify_assertions(extracted, dossier_ids))
