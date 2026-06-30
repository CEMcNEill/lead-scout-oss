"""The Qualifier protocol and a shared base for the agentic interior.

A qualifier owns the interior flow for its lead type: it researches with the
shared toolbox, assembles a grounded dossier, judges holistically, and (only if
warranted) drafts. However it runs internally, it must return the same shared
shape: a provenanced dossier, a Disposition referencing it by Claim id, and a
draft or None. That conformance is what preserves auditability.

BaseQualifier provides the two pieces every qualifier shares: the holistic judge
(an Opus-tier call constrained to the dossier and to real candidate contacts)
and the draft step. Each concrete qualifier supplies only `gather`: the research
orchestration particular to its lead type.
"""

from __future__ import annotations

from typing import Any, Protocol

from shared.contracts import (
    Claim,
    Disposition,
    DispositionKind,
    Draft,
    LeadRun,
    RunResult,
    Target,
)
from shared.model import ModelTier, parse_json
from shared.tools.toolbox import Toolbox

_JUDGE_SYSTEM = (
    "You are the qualifier judgment for a PostHog sales engine. Make a single "
    "holistic disposition for one lead, weighing the whole grounded dossier. No "
    "single axis disqualifies. You are given the team rubric, the lead type, the "
    "dossier as Claims (id, field, value, confidence), and a set of candidate "
    "contacts to engage. Choose the disposition and, if outreach is warranted, "
    "the single best contact to engage (which may not be the named lead). "
    "Reference the Claim ids that drive your decision. Return JSON: "
    '{"disposition": "call|self_serve|nurture|disqualify", "reasoning": str, '
    '"confidence": 0..1, "claim_refs": [claim_id, ...], '
    '"target_email": <one candidate email, or null>}.'
)


class Qualifier(Protocol):
    name: str
    lead_type: str

    def matches(self, record: dict[str, Any]) -> bool: ...

    def run(self, task_id: str, record: dict[str, Any], tools: Toolbox) -> RunResult: ...


class BaseQualifier:
    """Shared judge + draft. Concrete qualifiers override `gather` and set
    `name`, `lead_type`, and `angle`."""

    name: str = "base"
    lead_type: str = "base"
    angle: str = "use-case-led"
    # signal-specific judgment guidance appended to the judge prompt. The shared
    # rubric is the bar; this says how to weigh the dossier for this lead type
    # (e.g. a lookalike has no usage, so judge sales-led potential, not activity).
    judge_guidance: str = ""
    # signal-specific drafting guidance handed to the drafter for this lead type:
    # the playbook for what a good email of this kind leads with and offers (e.g. a
    # startup roll-off leads with the savings/discount opportunity, not just "your
    # credits are ending"). Frames structure only; the fact-check gate still governs
    # every claim.
    draft_guidance: str = ""
    # follow-up cadence: days to wait after each send before the next touch is due.
    # [] means single-touch (no follow-up). [4, 7] = follow up 4 days after the
    # first send, then 7 days after the second. len(cadence)+1 = max touches.
    followup_cadence_days: list[int] = []
    # how a follow-up of this lead type should read (shorter, a fresh angle, an easy
    # out). Charters override it via the SKILL "How to follow up" section; this is
    # the fallback. The fact-check gate still governs every claim a follow-up makes.
    followup_guidance: str = (
        "A brief, friendly nudge on the same thread, not a resend. Keep it shorter "
        "than the first touch, add one fresh angle or a lighter ask, do not repeat "
        "the earlier message, and make it easy to say no. Assert only what the "
        "dossier grounds."
    )

    def __init__(self, rubric: str) -> None:
        self.rubric = rubric

    @property
    def max_touches(self) -> int:
        return len(self.followup_cadence_days) + 1

    # --- to override -----------------------------------------------------

    def gather(
        self, task_id: str, record: dict[str, Any], tools: Toolbox
    ) -> tuple[list[Claim], list[dict[str, Any]]]:
        """Research the lead. Return (dossier, candidate_contacts).

        Each candidate contact is {name, email, role, is_named_lead}. The named
        lead must be among them so there is always a fallback target.
        """
        raise NotImplementedError

    def matches(self, record: dict[str, Any]) -> bool:
        """Convenience guard. The deterministic router is the source of truth for
        dispatch; this exists for qualifiers and the conformance suite."""
        return True

    # --- shared interior -------------------------------------------------

    def run(self, task_id: str, record: dict[str, Any], tools: Toolbox) -> RunResult:
        dossier, candidates = self.gather(task_id, record, tools)
        disposition = self.judge(dossier, candidates, tools)
        draft: Draft | None = None
        if disposition.disposition == DispositionKind.CALL:
            draft = tools.drafter.draft(
                dossier, disposition, self.angle, guidance=self.draft_guidance
            )
        return RunResult(dossier=dossier, disposition=disposition, draft=draft)

    def follow_up(self, run: LeadRun, touch_number: int, tools: Toolbox) -> Draft | None:
        """Draft the next touch in the sequence. Reuses the run's dossier and
        disposition -- no new research, no new judgment, just one drafter call -- so
        a follow-up costs one Opus draft + one fact-check. Threads onto touch 1 with
        a Re: subject and references prior touches so it reads as a nudge, not a
        resend. Returns None when the run is not a live call. The boundary fact-check
        gate still governs the draft; nothing is ever sent."""
        if run.disposition is None or run.disposition.disposition != DispositionKind.CALL:
            return None
        ordered = sorted(run.touches, key=lambda t: t.n)
        prior = [t.body for t in ordered if t.body]
        guidance = self._followup_guidance_text(touch_number, prior)
        draft = tools.drafter.draft(
            run.dossier, run.disposition, f"{self.angle}-followup", guidance=guidance
        )
        first_subject = ordered[0].subject if ordered else draft.subject
        subject = (
            first_subject if first_subject.lower().startswith("re:")
            else f"Re: {first_subject}"
        )
        return Draft(to=draft.to, subject=subject, body=draft.body,
                     angle=draft.angle, claims_used=draft.claims_used)

    def _followup_guidance_text(self, touch_number: int, prior_bodies: list[str]) -> str:
        import json as _json

        blocks = "\n\n---\n\n".join(prior_bodies) if prior_bodies else "(none)"
        return (
            f"{self.followup_guidance}\n\n"
            f"This is follow-up #{touch_number} on an existing thread. The "
            f"message(s) already sent (do NOT repeat them):\n{blocks}\n\n"
            f"(touch_number={_json.dumps(touch_number)})"
        )

    def judge(
        self,
        dossier: list[Claim],
        candidates: list[dict[str, Any]],
        tools: Toolbox,
    ) -> Disposition:
        dossier_ids = {c.id for c in dossier}
        claims_view = [
            {"id": c.id, "field": c.field, "value": c.value, "confidence": c.confidence}
            for c in dossier
        ]
        import json

        guidance = f"How to weigh this lead type:\n{self.judge_guidance}\n\n" if self.judge_guidance else ""
        prompt = (
            f"Rubric:\n{self.rubric}\n\n"
            f"Lead type: {self.lead_type}\n\n"
            f"{guidance}"
            f"Candidate contacts:\n```json\n{json.dumps(candidates, indent=2)}\n```\n\n"
            f"Dossier Claims:\n```json\n{json.dumps(claims_view, indent=2)}\n```"
        )
        resp = tools.model.complete(
            system=_JUDGE_SYSTEM,
            prompt=prompt,
            tier=ModelTier.QUALIFIER_JUDGMENT,
            step=f"{self.lead_type}.judgment",
        )
        payload = parse_json(resp.text)

        kind = DispositionKind(payload["disposition"])
        # keep only claim_refs that name real Claims (deterministic guard)
        claim_refs = [r for r in payload.get("claim_refs", []) if r in dossier_ids]
        confidence = float(payload.get("confidence", 0.0))
        target = _select_target(candidates, payload.get("target_email"))
        return Disposition(
            disposition=kind,
            reasoning=payload.get("reasoning", ""),
            confidence=confidence,
            claim_refs=claim_refs,
            target=target,
        )


def named_lead_candidate(record: dict[str, Any]) -> dict[str, Any]:
    """The named lead as a candidate contact. Always present so a target can
    always fall back to the person on the task."""
    lead = record.get("lead", {})
    return {
        "name": lead.get("name", ""),
        "email": lead.get("email"),
        "role": lead.get("title"),
        "is_named_lead": True,
    }


def dedup_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse candidates sharing an email, keeping the first (named lead wins
    because qualifiers list it first)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for c in candidates:
        key = (c.get("email") or c.get("name") or "").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _select_target(candidates: list[dict[str, Any]], target_email: str | None) -> Target | None:
    """Resolve the judge's chosen contact to a real candidate. Falls back to the
    named lead so a target is always grounded in a candidate the research found."""
    if not candidates:
        return None
    chosen = None
    if target_email:
        chosen = next((c for c in candidates if c.get("email") == target_email), None)
    if chosen is None:
        chosen = next((c for c in candidates if c.get("is_named_lead")), candidates[0])
    return Target(
        name=chosen.get("name", ""),
        email=chosen.get("email"),
        role=chosen.get("role"),
        is_named_lead=bool(chosen.get("is_named_lead", False)),
    )
