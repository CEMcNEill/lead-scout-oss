"""Spend-spike qualifier: routed 1:1 from the product-led matching_criteria signal
spend_spike.

A paying account whose spend is accelerating: MRR over $1k with a forecasted >50% increase this month.
"""

from __future__ import annotations

from shared.agentic import AgenticQualifier


class SpendSpikeQualifier(AgenticQualifier):
    name = "spend_spike"
    lead_type = "spend_spike"
    signal = "spend_spike"
    angle = "plg-spend-spike-led"
    followup_cadence_days = [3, 7]
    judge_guidance = (
        "The trajectory is the signal. Weight the rate of change and whether the growth "
        "implies a use case worth a conversation right now. "
    )
