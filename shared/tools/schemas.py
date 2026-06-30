"""Grounding-preserving tool interface for the agentic qualifier.

Exposes the toolbox research primitives as Anthropic tool schemas, plus a
`dispatch_tool` that calls the REAL (grounding) tool, accumulates its Claims and
candidates, and returns a COMPACT tool_result (claim summaries, never `raw`) — so
the model can cite grounded claims but cannot mint provenance. Two guards: bound
total invocations, and only enrich entities (emails/domains) already known from
the lead or the usage roster, so a draft can't be addressed to a guessed contact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from shared.contracts import Claim
from shared.model import ToolSpec
from shared.qualifier import named_lead_candidate

_MAX_ROSTER = 8  # bound buying-group enumeration, mirroring plg_base


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [],
            "additionalProperties": False}


TOOL_SCHEMAS: list[ToolSpec] = [
    ToolSpec(
        "usage_research",
        "Read this account's PostHog usage (event volume, products, trajectory, "
        "billing) and its internal user roster. Call once when the lead has an "
        "account; the roster tells you who else to enrich.",
        _obj({}),
    ),
    ToolSpec(
        "person_research",
        "Enrich one contact: seniority, role, budget ownership, likely pain. The "
        "email must be the named lead or someone on the usage roster.",
        _obj({"email": {"type": "string"}, "name": {"type": "string"}}),
    ),
    ToolSpec(
        "company_research",
        "Enrich the lead's company by domain: ICP fit, segment, buying signals, "
        "tech stack.",
        _obj({"domain": {"type": "string"}}),
    ),
    ToolSpec(
        "use_case_mapping",
        "Map the evidence gathered so far (usage, personas, company) to concrete "
        "PostHog use cases. Call after you have gathered usage and/or personas.",
        _obj({}),
    ),
]


@dataclass
class Accumulator:
    """State the agent loop fills via dispatched tools; the dossier/candidates it
    holds are what the qualifier returns to judge + draft."""

    record: dict[str, Any]
    task_id: str
    dossier: list[Claim] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    invocations: int = 0
    roster: list[dict[str, Any]] = field(default_factory=list)
    usage_raw: Any = None
    persona_raws: list[Any] = field(default_factory=list)
    company_raw: Any = None
    allowed_emails: set[str] = field(default_factory=set)
    allowed_domains: set[str] = field(default_factory=set)


def new_accumulator(record: dict[str, Any], task_id: str) -> Accumulator:
    acc = Accumulator(record=record, task_id=task_id,
                      candidates=[named_lead_candidate(record)])
    lead = record.get("lead", {}) or {}
    if lead.get("email"):
        acc.allowed_emails.add(str(lead["email"]).lower())
    if lead.get("domain"):
        acc.allowed_domains.add(str(lead["domain"]).lower())
    return acc


def _compact(claims: list[Claim]) -> str:
    return json.dumps([{"id": c.id, "field": c.field, "value": c.value,
                        "confidence": c.confidence} for c in claims])


def dispatch_tool(name: str, inp: dict[str, Any] | None, tools: Any, acc: Accumulator) -> str:
    """Run the real grounding tool the model chose; accumulate Claims/candidates;
    return a compact, citable summary (never raw)."""
    acc.invocations += 1
    inp = inp or {}
    if name == "usage_research":
        if not acc.record.get("account_ref"):
            return "no PostHog account on this lead; there is no usage to read"
        res = tools.usage_research.query(acc.record)
        acc.dossier += res.claims
        acc.usage_raw = res.raw
        acc.roster = tools.usage_research.roster(res) or []
        for m in acc.roster:
            if m.get("email"):
                acc.allowed_emails.add(str(m["email"]).lower())
        roster_view = [{"email": m.get("email"), "name": m.get("name"),
                        "role": m.get("role")} for m in acc.roster[:_MAX_ROSTER]]
        return _compact(res.claims) + "\nroster: " + json.dumps(roster_view)
    if name == "person_research":
        email = (inp.get("email") or "").strip()
        if email and email.lower() not in acc.allowed_emails:
            return (f"rejected: {email} is not a known contact. Enrich only the "
                    "named lead or someone on the usage roster.")
        res = tools.person_research.enrich({"email": inp.get("email"), "name": inp.get("name")})
        acc.dossier += res.claims
        acc.persona_raws.append(res.raw)
        lead_email = (acc.record.get("lead", {}) or {}).get("email")
        acc.candidates.append({
            "name": inp.get("name", ""),
            "email": inp.get("email"),
            "role": (res.raw or {}).get("title") or inp.get("role"),
            "is_named_lead": bool(email and lead_email
                                  and email.lower() == str(lead_email).lower()),
        })
        return _compact(res.claims)
    if name == "company_research":
        domain = (inp.get("domain") or "").strip()
        if domain and domain.lower() not in acc.allowed_domains:
            return f"rejected: {domain} is not the lead's company domain"
        res = tools.company_research.enrich(domain)
        acc.dossier += res.claims
        acc.company_raw = res.raw
        return _compact(res.claims)
    if name == "use_case_mapping":
        res = tools.use_case_mapping.map({
            "usage": acc.usage_raw,
            "personas": acc.persona_raws,
            "persona": acc.persona_raws[0] if acc.persona_raws else None,
            "company": acc.company_raw,
        })
        acc.dossier += res.claims
        return _compact(res.claims)
    return f"unknown tool: {name}"
