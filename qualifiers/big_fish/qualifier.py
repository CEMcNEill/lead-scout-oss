"""Big fish qualifier: routed 1:1 from the product-led matching_criteria signal
big_fish.

A large-headcount account (500+ or 1000+ employees) active on the free plan with no payment method. The signal is about the account, not the named person.
"""

from __future__ import annotations

from qualifiers.plg_base import AccountFirstQualifier


class BigFishQualifier(AccountFirstQualifier):
    name = "big_fish"
    lead_type = "big_fish"
    signal = "big_fish"
    angle = "plg-big-fish-led"
    judge_guidance = (
        "A large company active on the free plan with no payment method. Weight account scale "
        "and breadth of usage over firmographic polish. Real usage plus a buyable persona is "
        "a call; target the economic buyer or champion from the roster, not the IC who signed "
        "up. "
    )
