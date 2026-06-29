"""Scale-activation qualifier: routed 1:1 from the product-led matching_criteria signal
scale_activation.

An account that just activated the Scale plan and carries a high lead score.
"""

from __future__ import annotations

from qualifiers.plg_base import AccountFirstQualifier


class ScaleActivationQualifier(AccountFirstQualifier):
    name = "scale_activation"
    lead_type = "scale_activation"
    signal = "scale_activation"
    angle = "plg-scale-activation-led"
    judge_guidance = (
        "They have committed; the goal is to ensure they get value and expand. Weight what "
        "the Scale activation unlocks and who owns it. "
    )
