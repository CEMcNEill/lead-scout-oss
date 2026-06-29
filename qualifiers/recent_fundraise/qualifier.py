"""Recent-fundraise qualifier: routed 1:1 from the product-led matching_criteria signal
recent_fundraise.

A product-led account whose company recently raised funding.
"""

from __future__ import annotations

from qualifiers.plg_base import ProspectQualifier


class RecentFundraiseQualifier(ProspectQualifier):
    name = "recent_fundraise"
    lead_type = "recent_fundraise"
    signal = "recent_fundraise"
    angle = "fundraise-timing-led"
    use_usage = True
    judge_guidance = (
        "New budget and scaling pressure make this a timing play. Usage may be thin; weight "
        "company fit and the scaling need the raise implies over current activity. "
    )
