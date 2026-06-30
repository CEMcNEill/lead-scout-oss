"""New-customer qualifier: routed 1:1 from the product-led matching_criteria signal
new_customer.

A customer who just landed with a large first invoice (2000+).
"""

from __future__ import annotations

from shared.agentic import AgenticQualifier


class NewCustomerQualifier(AgenticQualifier):
    name = "new_customer"
    lead_type = "new_customer"
    signal = "new_customer"
    angle = "plg-new-customer-led"
    followup_cadence_days = [4, 9]
    judge_guidance = (
        "The relationship is new; the goal is fast activation and an expansion path. Weight "
        "early activation breadth and the owner who can drive adoption. "
    )
