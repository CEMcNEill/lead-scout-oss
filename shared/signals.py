"""Salesforce routing signals: the closed vocabularies the router maps 1:1.

Two deterministic maps, both keyed on lowercased Salesforce text so routing never
depends on a model:

  - CATEGORY_BY_SOURCE: the lead's top-level category from its source token. The
    token is the Task `Lead_Source__c`, else the bracketed Subject tag, else the
    Contact `LeadSource`. Inbound channels (contact form, sales mailbox, slack /
    teams inbound, zendesk, employee referral, lost-opp revival) all collapse to
    `inbound` because the message is the signal there. Outbound channels collapse
    to `outbound`, which the engine does not handle (excluded before routing).

  - SIGNAL_BY_MATCHING_CRITERIA: for product-led leads, the `matching_criteria__c`
    text is a closed set; each exact value maps to the product-led signal that
    decides how the lead is handled. A value not in this set leaves the signal
    unresolved (None) and the router sends it to the product-led fallback.

These are the only place the raw Salesforce strings live. Qualifiers and the
router speak in normalized category/signal labels.
"""

from __future__ import annotations

# --- top-level category ---------------------------------------------------

PRODUCT_LED = "product-led"
INBOUND = "inbound"
ONBOARDING = "onboarding"
OUTBOUND = "outbound"

# source token (lowercased) -> category. Tokens come from Lead_Source__c values
# and from bracketed Subject tags; both vocabularies are covered.
CATEGORY_BY_SOURCE: dict[str, str] = {
    # product-led
    "product-led": PRODUCT_LED,
    "plg": PRODUCT_LED,
    "self-serve": PRODUCT_LED,
    # inbound: someone reached out, or a person-level inbound channel
    "contact sales form": INBOUND,
    "contact us": INBOUND,
    "default contact form": INBOUND,
    "sales mailbox": INBOUND,
    "slack - inbound": INBOUND,
    "microsoft teams - inbound": INBOUND,
    "zendesk": INBOUND,
    "employee referral": INBOUND,
    "lost opportunity revival": INBOUND,
    # onboarding referral is product-led: the onboarding team flags an account that
    # is actively using the product now, so it routes through the product-led path
    # (its matching_criteria carries the onboarding_referral signal).
    "onboarding referral": PRODUCT_LED,
    "onboarding": ONBOARDING,
    # outbound: rep/tool initiated; not handled by this engine
    "slack - outbound": OUTBOUND,
    "lemlist": OUTBOUND,
}

# Subject-tag prefixes (lowercased, without brackets) that mark an outbound task.
# Used to exclude outbound at the poll so it never reaches routing.
OUTBOUND_SUBJECT_TAGS: tuple[str, ...] = ("slack - outbound", "lemlist")


# --- product-led signal ---------------------------------------------------

# matching_criteria__c exact value (lowercased) -> product-led signal label.
# A closed set: every value the org assigns to a product-led lead.
SIGNAL_BY_MATCHING_CRITERIA: dict[str, str] = {
    # big fish: large headcount, no payment method (free-plan whale)
    "big fish alert: 500+ employees, no payment method": "big_fish",
    "big fish alert: 1000+ employees, no payment method": "big_fish",
    # job switcher: a prior PostHog user/champion moved to a new company
    "job switcher": "job_switcher",
    # mrr fit: paying account meeting MRR + firmographic thresholds
    "$500–1667 mrr, >50 employees, >7 users, icp country, paying 3+ months": "mrr_fit",
    # spend spike: paying customer with accelerating spend
    "mrr > $1k + >50% forecasted spend increase this month": "spend_spike",
    # startup rolloff: startup program ending, high credit spend
    "startup rolloff + high credit spend": "startup_rolloff",
    "used >50% of startup credits + last invoice >$5k": "startup_rolloff",
    # new customer: just landed with a large first invoice
    "new customer, 2000+ first invoice": "new_customer",
    # recent fundraise: company raised money
    "recent fundraising activity": "recent_fundraise",
    # lookalikes: resemble good accounts, no usage of their own yet
    "growth-fit lookalikes": "lookalike",
    "ocean.io growth-fit lookalikes": "lookalike",
    "revenue fit lookalikes": "lookalike",
    "ocean.io hot lookalikes": "lookalike",
    # trust center / nda: late-stage buying intent
    "requested access to trust center and signed an nda": "trust_center_nda",
    # unmanaged ticket: large customer with no CSM raised a support ticket
    "20k+ unmanaged customer raises ticket": "unmanaged_ticket",
    # scale activation: just activated the Scale plan with a high lead score
    "activated scale plan and has high lead score": "scale_activation",
    # eng headcount growth: engineering team is growing
    "engineer headcount growth": "eng_headcount_growth",
    # onboarding referral: the onboarding team flagged an active customer to help
    # activate. Product-led (using the product now); handled by the onboarding
    # qualifier rather than its own.
    "referral from onboarding team": "onboarding_referral",
}

# Every product-led signal label, for the registry and tests to enumerate.
PRODUCT_LED_SIGNALS: tuple[str, ...] = (
    "big_fish",
    "mrr_fit",
    "job_switcher",
    "spend_spike",
    "startup_rolloff",
    "new_customer",
    "recent_fundraise",
    "lookalike",
    "trust_center_nda",
    "unmanaged_ticket",
    "scale_activation",
    "eng_headcount_growth",
    # product-led, but reuses the activation-led onboarding qualifier
    "onboarding_referral",
)


def resolve_category(source_token: str | None) -> str | None:
    """Normalize a raw source token (Lead_Source__c / Subject tag / LeadSource) to
    a category label, or None if the token is unknown."""
    if not source_token:
        return None
    return CATEGORY_BY_SOURCE.get(source_token.strip().lower())


def resolve_signal(matching_criteria: str | None) -> str | None:
    """Normalize a raw matching_criteria__c value to a product-led signal label,
    or None if the value is not in the closed set."""
    if not matching_criteria:
        return None
    return SIGNAL_BY_MATCHING_CRITERIA.get(matching_criteria.strip().lower())
