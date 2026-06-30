"""Outbound qualifier: a rep/tool-initiated lead from an external lemlist or
slack sequence.

Outbound is excluded at the poll by default; SF_INCLUDE_OUTBOUND opts a rep in,
and the active-sequence hard-stop suppresses any lead still in a running
sequence before this qualifier is reached. What lands here is a named prospect
worth a human touch (the sequence is done or paused), with little or no PostHog
usage of their own, so this uses the prospect flow: enrich the person and
company, fold in usage only if an account already exists, and map the use case
from persona + company. Deterministic for now; Phase 3 makes it agentic.
"""

from __future__ import annotations

from typing import Any

from qualifiers.plg_base import ProspectQualifier


class OutboundQualifier(ProspectQualifier):
    name = "outbound"
    lead_type = "outbound"
    angle = "outbound-prospect-led"
    use_usage = True  # fold in usage if the contact already maps to an account
    followup_cadence_days = [3, 6]  # two light nudges after the first human touch
    judge_guidance = (
        "A cold-ish prospect the rep already chose to sequence. Weight company fit and the "
        "use case the persona implies; current PostHog usage is usually thin or absent, so do "
        "not penalize its absence. The sequence is done or paused (live ones are hard-stopped). "
    )
    draft_guidance = (
        "This is a first human touch after an automated sequence, not a cold open. Keep it "
        "short and specific to what their company/role suggests they are trying to do; lead "
        "with the relevant PostHog use case, reference one concrete, grounded detail, and close "
        "soft with an offer to chat. No hard calendar push, no rehashing of the sequence."
    )

    def matches(self, record: dict[str, Any]) -> bool:
        return record.get("category") == "outbound"
