"""Job-switcher qualifier: routed 1:1 from the product-led matching_criteria signal
job_switcher.

A known contact (a prior PostHog user or champion) who has moved to a new company.
"""

from __future__ import annotations

from shared.agentic import AgenticQualifier


class JobSwitcherQualifier(AgenticQualifier):
    name = "job_switcher"
    lead_type = "job_switcher"
    signal = "job_switcher"
    angle = "job-switcher-reintro-led"
    followup_cadence_days = [5, 11]
    judge_guidance = (
        "The prior relationship is the asset. Weight the strength of that history and whether "
        "the new company is a fit; this is a warm reintroduction, not a cold pitch. "
    )
