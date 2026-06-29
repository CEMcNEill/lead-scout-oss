"""MRR-fit qualifier: routed 1:1 from the product-led matching_criteria signal
mrr_fit.

A paying product-led account that already clears the team's MRR and firmographic thresholds (roughly $500-1667 MRR, >50 employees, >7 users, ICP country, paying 3+ months). A sales-led expansion candidate.
"""

from __future__ import annotations

from qualifiers.plg_base import AccountFirstQualifier


class MrrFitQualifier(AccountFirstQualifier):
    name = "mrr_fit"
    lead_type = "mrr_fit"
    signal = "mrr_fit"
    angle = "plg-mrr-fit-led"
    judge_guidance = (
        "The account already fits; the question is the next tier. Weight expansion readiness "
        "and which persona owns the growing use case. "
    )
