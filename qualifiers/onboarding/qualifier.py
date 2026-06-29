"""Onboarding qualifier: a newly assigned customer to activate.

Reasons from activation signals in usage plus the named contact's persona, maps
what the customer is trying to do, and drafts an activation-led message to the
named contact.
"""

from __future__ import annotations

from typing import Any

from shared.contracts import Claim
from shared.qualifier import BaseQualifier, named_lead_candidate
from shared.tools.toolbox import Toolbox


class OnboardingQualifier(BaseQualifier):
    name = "onboarding"
    lead_type = "onboarding"
    angle = "onboarding-activation-led"

    def matches(self, record: dict[str, Any]) -> bool:
        return record.get("trigger") in {"onboarding", "onboarding_assigned", "new_customer"}

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

        account_ref = record.get("account_ref")
        usage_raw: Any = None
        if account_ref:
            usage = tools.usage_research.query(record)
            dossier += usage.claims
            usage_raw = usage.raw

        uc = tools.use_case_mapping.map({"usage": usage_raw, "persona": person.raw})
        dossier += uc.claims

        return dossier, [named_lead_candidate(record)]
