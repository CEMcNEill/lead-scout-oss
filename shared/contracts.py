"""Core contracts shared across the system.

These are the shapes the spec makes mandatory: the grounded Claim, the
Disposition every qualifier returns, the Draft the drafter stages, and the
LeadRun the ledger persists. Nothing here names a person; rep identity flows
through RepConfig at runtime.

Everything is a plain dataclass with explicit to_dict/from_dict so the ledger
can store dossiers, dispositions, and drafts as inspectable JSON and read them
back without a serialization framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# --- enums ---------------------------------------------------------------


class DispositionKind(str, Enum):
    CALL = "call"
    SELF_SERVE = "self_serve"
    NURTURE = "nurture"
    DISQUALIFY = "disqualify"


class Product(str, Enum):
    """PostHog products a use case can map to (use_case_mapping)."""

    ANALYTICS = "analytics"
    REPLAY = "replay"
    FLAGS = "flags"
    EXPERIMENTS = "experiments"
    SURVEYS = "surveys"
    DATA_WAREHOUSE = "data_warehouse"
    LLM_ANALYTICS = "llm_analytics"
    ERROR_TRACKING = "error_tracking"
    WEB_ANALYTICS = "web_analytics"


class TriggerSource(str, Enum):
    BATCH = "batch"
    WEBHOOK = "webhook"
    MANUAL = "manual"


class RunStatus(str, Enum):
    STAGED_FOR_REVIEW = "staged_for_review"
    BLOCKED = "blocked"
    ERROR = "error"


# --- grounded evidence ---------------------------------------------------


@dataclass
class Claim:
    """A single grounded fact.

    `raw` must trace to fetcher output; the synthesis layer may not assert a
    Claim the fetchers did not return. `id` is unique within a run so a
    Disposition's reasoning can reference it.
    """

    id: str
    field: str
    value: Any
    source: str
    raw: Any
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "raw": self.raw,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Claim":
        return cls(
            id=d["id"],
            field=d["field"],
            value=d["value"],
            source=d["source"],
            raw=d["raw"],
            confidence=d["confidence"],
        )


@dataclass
class UseCaseClaim:
    """A grounded judgment about a probable use case and the product it maps to.

    A specialized Claim emitted by use_case_mapping. `evidence` is the message
    phrase or usage rows that ground it; `owner_persona` is who in the account
    the use case sits with (product-led).
    """

    id: str
    use_case: str
    product: Product
    evidence: Any
    owner_persona: str | None
    confidence: float
    source: str = "use_case_mapping"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "use_case": self.use_case,
            "product": self.product.value,
            "evidence": self.evidence,
            "owner_persona": self.owner_persona,
            "confidence": self.confidence,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UseCaseClaim":
        return cls(
            id=d["id"],
            use_case=d["use_case"],
            product=Product(d["product"]),
            evidence=d["evidence"],
            owner_persona=d.get("owner_persona"),
            confidence=d["confidence"],
            source=d.get("source", "use_case_mapping"),
        )


# --- target contact ------------------------------------------------------


@dataclass
class Target:
    """Who to actually engage. For product-led leads this may differ from the
    named lead on the task."""

    name: str
    email: str | None = None
    role: str | None = None
    is_named_lead: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "is_named_lead": self.is_named_lead,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Target":
        return cls(
            name=d["name"],
            email=d.get("email"),
            role=d.get("role"),
            is_named_lead=d.get("is_named_lead", True),
        )


# --- disposition ---------------------------------------------------------


@dataclass
class Disposition:
    """The qualifier's holistic judgment. `reasoning` references Claims by id so
    the call is auditable and the override loop has something concrete to
    correct."""

    disposition: DispositionKind
    reasoning: str
    confidence: float
    claim_refs: list[str] = field(default_factory=list)
    target: Target | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "claim_refs": list(self.claim_refs),
            "target": self.target.to_dict() if self.target else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Disposition":
        return cls(
            disposition=DispositionKind(d["disposition"]),
            reasoning=d["reasoning"],
            confidence=d["confidence"],
            claim_refs=list(d.get("claim_refs", [])),
            target=Target.from_dict(d["target"]) if d.get("target") else None,
        )


# --- draft ---------------------------------------------------------------


@dataclass
class Draft:
    """Staged outreach. In Phase 1 this is an object the shell writes to a stub
    sink; in Phase 1.5 it becomes a real Gmail draft addressed to the target.

    `claims_used` is the drafter's hint of which Claims grounded each factual
    statement. The boundary fact-check gate verifies assertions independently of
    this hint, but it helps the drafter stay honest.
    """

    to: str | None
    subject: str
    body: str
    angle: str
    claims_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "to": self.to,
            "subject": self.subject,
            "body": self.body,
            "angle": self.angle,
            "claims_used": list(self.claims_used),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Draft":
        return cls(
            to=d.get("to"),
            subject=d["subject"],
            body=d["body"],
            angle=d["angle"],
            claims_used=list(d.get("claims_used", [])),
        )


# --- qualifier output ----------------------------------------------------


@dataclass
class RunResult:
    """What a qualifier returns. The mandatory shared shape: a provenanced
    dossier, a Disposition referencing it, and a draft (or None)."""

    dossier: list[Claim]
    disposition: Disposition
    draft: Draft | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dossier": [c.to_dict() for c in self.dossier],
            "disposition": self.disposition.to_dict(),
            "draft": self.draft.to_dict() if self.draft else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunResult":
        return cls(
            dossier=[Claim.from_dict(c) for c in d.get("dossier", [])],
            disposition=Disposition.from_dict(d["disposition"]),
            draft=Draft.from_dict(d["draft"]) if d.get("draft") else None,
        )


# --- runtime config ------------------------------------------------------


@dataclass
class RepConfig:
    """Resolves rep identity at runtime so nothing hardcodes a person.

    Secrets are referenced, never held: sf_credential_ref and gmail_account name
    a Keychain entry / account, they are not the tokens themselves.
    """

    rep_id: str
    sf_user_id: str
    sf_credential_ref: str
    gmail_account: str
    voice_profile_ref: str
    signature: str
    slack_post_target: str
    budget_cap_usd: float
    calendar_url: str = ""
    rubric_tuning: dict[str, Any] = field(default_factory=dict)


@dataclass
class TriggerMeta:
    source: TriggerSource
    timestamp: str


# --- ledger record -------------------------------------------------------


@dataclass
class Route:
    lead_type: str
    qualifier: str

    def to_dict(self) -> dict[str, Any]:
        return {"lead_type": self.lead_type, "qualifier": self.qualifier}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Route":
        return cls(lead_type=d["lead_type"], qualifier=d["qualifier"])


@dataclass
class Outcome:
    replied: bool | None = None
    reply_sentiment: str | None = None
    meeting_booked: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "replied": self.replied,
            "reply_sentiment": self.reply_sentiment,
            "meeting_booked": self.meeting_booked,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Outcome":
        return cls(
            replied=d.get("replied"),
            reply_sentiment=d.get("reply_sentiment"),
            meeting_booked=d.get("meeting_booked"),
        )


@dataclass
class Touch:
    """One outreach in a lead's sequence. Append-only history on the LeadRun: touch
    1 is the first staged draft, touch 2+ are follow-ups. `sent_at` is stamped when
    the slow loop detects the rep actually sent it (a draft staged but never sent
    has sent_at=None and never advances the sequence)."""

    n: int
    subject: str
    body: str
    staged_at: str | None = None
    sent_at: str | None = None
    draft_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "subject": self.subject,
            "body": self.body,
            "staged_at": self.staged_at,
            "sent_at": self.sent_at,
            "draft_ref": self.draft_ref,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Touch":
        return cls(
            n=d["n"],
            subject=d.get("subject", ""),
            body=d.get("body", ""),
            staged_at=d.get("staged_at"),
            sent_at=d.get("sent_at"),
            draft_ref=d.get("draft_ref"),
        )


@dataclass
class CostEntry:
    """One metered spend: a Claude call or a paid tool call."""

    step: str
    kind: str  # "model" | "tool"
    detail: str  # model id, or tool/provider name
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "kind": self.kind,
            "detail": self.detail,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "usd": self.usd,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CostEntry":
        return cls(
            step=d["step"],
            kind=d["kind"],
            detail=d["detail"],
            tokens_in=d.get("tokens_in", 0),
            tokens_out=d.get("tokens_out", 0),
            usd=d.get("usd", 0.0),
        )


@dataclass
class Cost:
    entries: list[CostEntry] = field(default_factory=list)

    @property
    def total_usd(self) -> float:
        return round(sum(e.usd for e in self.entries), 6)

    def add(self, entry: CostEntry) -> None:
        self.entries.append(entry)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "total_usd": self.total_usd,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Cost":
        return cls(entries=[CostEntry.from_dict(e) for e in d.get("entries", [])])


@dataclass
class LeadRun:
    """The ledger record. Output of process_lead_run plus the fields the human
    and slow loops fill in later. Phase 1.5 fields default to None."""

    id: str
    task_id: str
    rep_id: str
    trigger_source: TriggerSource
    ts: str
    route: Route
    status: RunStatus
    dossier: list[Claim] = field(default_factory=list)
    hard_stops: list[str] = field(default_factory=list)
    disposition: Disposition | None = None
    staged_draft: Draft | None = None
    cost: Cost = field(default_factory=Cost)

    # assertions the boundary gate flagged/stripped before staging (zero normally)
    factcheck_flags: list[str] = field(default_factory=list)

    # reference to the staged draft (Gmail draft URL); shown in the Slack card
    staged_draft_ref: str | None = None

    # version stamps so learning can attribute outcomes and roll back
    voice_profile_version: str | None = None
    rubric_version: str | None = None
    model_policy_version: str | None = None

    # Phase 1.5 fields, written by the human and slow loops
    slack_thread_ref: str | None = None
    human_disposition: str | None = None
    human_rationale: str | None = None
    # ts of the latest rep reply we have already acknowledged, so the ack fires
    # once per piece of feedback rather than every sweep.
    acked_reply_ts: str | None = None
    sent_draft: Draft | None = None
    draft_diff: str | None = None
    # the Gmail thread the staged draft was sent into, learned when the slow loop
    # matches the sent item. The follow-up loop reads this thread to detect a reply
    # and stages each follow-up into it. None until a send is matched.
    thread_id: str | None = None
    # append-only outreach history (touch 1 = first draft, 2+ = follow-ups) and the
    # time the next follow-up is due (None = nothing due / sequence complete or
    # replied). Both written by the slow loop; the follow-up poll reads them.
    touches: list[Touch] = field(default_factory=list)
    next_touch_due: str | None = None
    outcome: Outcome = field(default_factory=Outcome)

    # for runs that errored
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "rep_id": self.rep_id,
            "trigger_source": self.trigger_source.value,
            "ts": self.ts,
            "route": self.route.to_dict(),
            "status": self.status.value,
            "dossier": [c.to_dict() for c in self.dossier],
            "hard_stops": list(self.hard_stops),
            "disposition": self.disposition.to_dict() if self.disposition else None,
            "staged_draft": self.staged_draft.to_dict() if self.staged_draft else None,
            "cost": self.cost.to_dict(),
            "factcheck_flags": list(self.factcheck_flags),
            "staged_draft_ref": self.staged_draft_ref,
            "voice_profile_version": self.voice_profile_version,
            "rubric_version": self.rubric_version,
            "model_policy_version": self.model_policy_version,
            "slack_thread_ref": self.slack_thread_ref,
            "human_disposition": self.human_disposition,
            "human_rationale": self.human_rationale,
            "acked_reply_ts": self.acked_reply_ts,
            "sent_draft": self.sent_draft.to_dict() if self.sent_draft else None,
            "draft_diff": self.draft_diff,
            "thread_id": self.thread_id,
            "touches": [t.to_dict() for t in self.touches],
            "next_touch_due": self.next_touch_due,
            "outcome": self.outcome.to_dict(),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LeadRun":
        return cls(
            id=d["id"],
            task_id=d["task_id"],
            rep_id=d["rep_id"],
            trigger_source=TriggerSource(d["trigger_source"]),
            ts=d["ts"],
            route=Route.from_dict(d["route"]),
            status=RunStatus(d["status"]),
            dossier=[Claim.from_dict(c) for c in d.get("dossier", [])],
            hard_stops=list(d.get("hard_stops", [])),
            disposition=Disposition.from_dict(d["disposition"]) if d.get("disposition") else None,
            staged_draft=Draft.from_dict(d["staged_draft"]) if d.get("staged_draft") else None,
            cost=Cost.from_dict(d.get("cost", {})),
            factcheck_flags=list(d.get("factcheck_flags", [])),
            staged_draft_ref=d.get("staged_draft_ref"),
            voice_profile_version=d.get("voice_profile_version"),
            rubric_version=d.get("rubric_version"),
            model_policy_version=d.get("model_policy_version"),
            slack_thread_ref=d.get("slack_thread_ref"),
            human_disposition=d.get("human_disposition"),
            human_rationale=d.get("human_rationale"),
            acked_reply_ts=d.get("acked_reply_ts"),
            sent_draft=Draft.from_dict(d["sent_draft"]) if d.get("sent_draft") else None,
            draft_diff=d.get("draft_diff"),
            thread_id=d.get("thread_id"),
            touches=[Touch.from_dict(t) for t in d.get("touches", [])],
            next_touch_due=d.get("next_touch_due"),
            outcome=Outcome.from_dict(d.get("outcome", {})),
            error=d.get("error"),
        )
