"""Shared research flows for the product-led qualifier family.

Every product-led signal handles the lead with one of two flows; a concrete
qualifier picks a base, sets its `name`/`lead_type`/`signal`/`angle`, and writes a
SKILL.md charter. The signal-specific judgment and framing come from `lead_type`
(keys the judge step) and `angle` (frames the draft); the research shape is shared.

  AccountFirstQualifier
      The account is the unit. Pull usage heavily, discover the buying group from
      the internal user roster, enrich the company, map live use cases. The judge
      may target a contact other than the named lead. Used by the signals that
      describe a real, active account (big fish, mrr fit, spend spike, startup
      rolloff, new customer, unmanaged ticket, scale activation).

  ProspectQualifier
      The named person is the target and there is little or no PostHog usage to
      reason from. Enrich the person and company, optionally fold in usage if an
      account already exists, and map the use case from persona + company. Used by
      the signals that are about potential rather than current usage (lookalike,
      recent fundraise, eng headcount growth, job switcher). `use_usage = False`
      forces a pure company-analysis flow (lookalikes have no account at all).
"""

from __future__ import annotations

from typing import Any

from shared.contracts import Claim
from shared.qualifier import BaseQualifier, dedup_candidates, named_lead_candidate
from shared.tools.toolbox import Toolbox

_MAX_ROSTER = 8  # bound buying-group enrichment so per-run cost stays predictable


class AccountFirstQualifier(BaseQualifier):
    """Account-first product-led flow: usage + roster + buying-group discovery."""

    signal: str = ""

    def matches(self, record: dict[str, Any]) -> bool:
        return record.get("category") == "product-led" and record.get("signal") == self.signal

    def gather(
        self, task_id: str, record: dict[str, Any], tools: Toolbox
    ) -> tuple[list[Claim], list[dict[str, Any]]]:
        dossier: list[Claim] = []

        crm = tools.crm_context.read(task_id)
        dossier += crm.claims

        account_ref = record.get("account_ref")
        # pass the whole record so the usage fetcher can resolve the right account
        usage = tools.usage_research.query(record) if account_ref else None
        if usage:
            dossier += usage.claims
        roster = tools.usage_research.roster(usage) if usage else []

        # buying-group discovery: enrich the roster, build candidate contacts
        candidates: list[dict[str, Any]] = [named_lead_candidate(record)]
        persona_raws: list[Any] = []
        for member in roster[:_MAX_ROSTER]:
            person = tools.person_research.enrich(
                {"email": member.get("email"), "name": member.get("name")}
            )
            dossier += person.claims
            persona_raws.append(person.raw)
            candidates.append(
                {
                    "name": member.get("name", ""),
                    "email": member.get("email"),
                    "role": (person.raw or {}).get("title") or member.get("role"),
                    "is_named_lead": member.get("email") == record.get("lead", {}).get("email"),
                }
            )
        candidates = dedup_candidates(candidates)

        domain = record.get("lead", {}).get("domain")
        if domain:
            company = tools.company_research.enrich(domain)
            dossier += company.claims

        uc = tools.use_case_mapping.map(
            {"usage": usage.raw if usage else None, "personas": persona_raws}
        )
        dossier += uc.claims

        return dossier, candidates


class ProspectQualifier(BaseQualifier):
    """Prospect flow: the named person is the target and there is little or no
    usage. Enrich person + company, optionally fold in usage, map the use case."""

    signal: str = ""
    use_usage: bool = True  # fold in usage if an account already exists

    def matches(self, record: dict[str, Any]) -> bool:
        return record.get("category") == "product-led" and record.get("signal") == self.signal

    def gather(
        self, task_id: str, record: dict[str, Any], tools: Toolbox
    ) -> tuple[list[Claim], list[dict[str, Any]]]:
        dossier: list[Claim] = []

        crm = tools.crm_context.read(task_id)
        dossier += crm.claims

        lead = record.get("lead", {})
        person = tools.person_research.enrich(
            {"email": lead.get("email"), "name": lead.get("name")}
        )
        dossier += person.claims

        domain = lead.get("domain")
        company_raw: Any = None
        if domain:
            company = tools.company_research.enrich(domain)
            dossier += company.claims
            company_raw = company.raw

        usage_raw: Any = None
        if self.use_usage and record.get("account_ref"):
            usage = tools.usage_research.query(record)
            dossier += usage.claims
            usage_raw = usage.raw

        uc = tools.use_case_mapping.map(
            {"persona": person.raw, "company": company_raw, "usage": usage_raw}
        )
        dossier += uc.claims

        return dossier, [named_lead_candidate(record)]
