"""Lookalike qualifier: routed 1:1 from the product-led matching_criteria signal
lookalike.

A company surfaced because it resembles good accounts (growth-fit, revenue-fit,
or hot lookalikes). It usually has no PostHog usage, so it is qualified on company
analysis. But a lookalike can also turn out to be an active account: when an
account resolves, its real usage is folded in and weighed.
"""

from __future__ import annotations

from shared.agentic import AgenticQualifier


class LookalikeQualifier(AgenticQualifier):
    name = "lookalike"
    lead_type = "lookalike"
    signal = "lookalike"
    angle = "lookalike-fit-led"
    followup_cadence_days = [7]
    judge_guidance = (
        "A lookalike is sourced by resemblance to good accounts, not by usage, so it "
        "usually has none. If the dossier carries no PostHog usage, analyze the company "
        "and decide whether there is real potential for a sales-led engagement that gets "
        "them over $2k MRR quickly; if the fit hypothesis is weak, nurture rather than "
        "force a call. If the dossier DOES carry usage (the lookalike turned out to be an "
        "active account), weigh that usage heavily - real events, paid invoices, and "
        "products in use can make this a clear call regardless of the lookalike label. "
    )
