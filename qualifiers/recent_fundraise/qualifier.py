"""Recent-fundraise qualifier: routed 1:1 from the product-led matching_criteria signal
recent_fundraise.

A product-led account whose company recently raised funding.
"""

from __future__ import annotations

from shared.agentic import AgenticQualifier


class RecentFundraiseQualifier(AgenticQualifier):
    name = "recent_fundraise"
    lead_type = "recent_fundraise"
    signal = "recent_fundraise"
    angle = "fundraise-timing-led"
    followup_cadence_days = [5, 10]
    judge_guidance = (
        "New budget and scaling pressure make this a timing play. Usage may be thin; weight "
        "company fit and the scaling need the raise implies over current activity. "
    )
