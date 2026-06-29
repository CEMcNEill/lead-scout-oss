"""Job-switcher qualifier: routed 1:1 from the product-led matching_criteria signal
job_switcher.

A known contact (a prior PostHog user or champion) who has moved to a new company.
"""

from __future__ import annotations

from qualifiers.plg_base import ProspectQualifier


class JobSwitcherQualifier(ProspectQualifier):
    name = "job_switcher"
    lead_type = "job_switcher"
    signal = "job_switcher"
    angle = "job-switcher-reintro-led"
    use_usage = True
    judge_guidance = (
        "The prior relationship is the asset. Weight the strength of that history and whether "
        "the new company is a fit; this is a warm reintroduction, not a cold pitch. "
    )
