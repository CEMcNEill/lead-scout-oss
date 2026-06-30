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
    draft_guidance = (
        "Structure, in order:\n"
        "1. Open with a friendly heads-up: the startup-plan credits expire on the "
        "known date, after which usage is billed. Flag it early so there are no "
        "surprises and they have time to decide.\n"
        "2. Pivot to the value - this is the lead, not just 'your credits are ending'. "
        "Their usage has grown enough that moving to a pre-paid plan likely unlocks a "
        "volume discount: frame it as locking in a lower rate rather than topping up "
        "credits at the standard rate, and meaningful savings over a year at their "
        "scale. State a specific discount percentage ONLY if a Claim grounds it; "
        "otherwise keep it qualitative ('a volume discount') and offer to share the "
        "specifics on a call.\n"
        "3. Then offer cost-control help as an optional second path: PostHog is "
        "usage-based and a little tuning goes a long way - point to the estimating "
        "usage and reducing costs guide, the spend tab in billing, and the AI wizard "
        "audit (npx @posthog/wizard@latest audit).\n"
        "4. Close soft: happy to chat about their options if they are interested. No "
        "hard calendar push.\n"
        "Keep it warm and low-pressure; the recipient stays in control of the path."
    )
