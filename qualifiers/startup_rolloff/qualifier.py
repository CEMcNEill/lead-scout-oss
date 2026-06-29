"""Startup-rolloff qualifier: routed 1:1 from the product-led matching_criteria signal
startup_rolloff.

A startup whose program credits are ending, with high credit spend (or >50% of credits used and a last invoice over $5k).
"""

from __future__ import annotations

from qualifiers.plg_base import AccountFirstQualifier


class StartupRolloffQualifier(AccountFirstQualifier):
    name = "startup_rolloff"
    lead_type = "startup_rolloff"
    signal = "startup_rolloff"
    angle = "plg-rolloff-led"
    judge_guidance = (
        "A re-evaluation moment, not a disqualifier. Weight the live use case and whether an "
        "owner can decide on a paid plan as credits run out. "
    )
