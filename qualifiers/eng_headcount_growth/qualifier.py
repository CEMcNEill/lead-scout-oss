"""Engineering-headcount-growth qualifier: routed 1:1 from the product-led matching_criteria signal
eng_headcount_growth.

A product-led account whose engineering headcount is growing.
"""

from __future__ import annotations

from shared.agentic import AgenticQualifier


class EngHeadcountGrowthQualifier(AgenticQualifier):
    name = "eng_headcount_growth"
    lead_type = "eng_headcount_growth"
    signal = "eng_headcount_growth"
    angle = "eng-growth-led"
    followup_cadence_days = [6, 12]
    judge_guidance = (
        "Growth implies new tooling and collaboration needs. Usage may be thin; weight the "
        "growth signal and company fit, and lead with what a scaling engineering team needs. "
    )
