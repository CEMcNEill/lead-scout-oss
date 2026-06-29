"""Inbound qualifier.

The named person on an inbound task is the target. Research reasons from the
inbound message plus the persona and company, maps the use case from what they
said, and (if an account already exists) folds in usage. Drafts to the named
lead, leading with the use case the message implies.
"""

from __future__ import annotations

from typing import Any

from shared.contracts import Claim
from shared.qualifier import BaseQualifier, named_lead_candidate
from shared.tools.toolbox import Toolbox


class InboundQualifier(BaseQualifier):
    name = "inbound"
    lead_type = "inbound"
    angle = "inbound-use-case-led"

    def matches(self, record: dict[str, Any]) -> bool:
        return bool(record.get("inbound_message")) or record.get("trigger") in {
            "inbound", "inbound_inquiry", "demo_request", "contact_us"
        }

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

        evidence = {
            "message": record.get("inbound_message"),
            "persona": person.raw,
            "company": company_raw,
        }
        uc = tools.use_case_mapping.map(evidence)
        dossier += uc.claims

        # an inbound lead may already map to an account; if so, fold in usage
        account_ref = record.get("account_ref")
        if account_ref:
            usage = tools.usage_research.query(record)
            dossier += usage.claims

        return dossier, [named_lead_candidate(record)]
