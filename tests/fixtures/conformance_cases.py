"""The fixed conformance test set: one or more cases per qualifier.

Each case pairs a small world with a scripted model that drives a faithful
interior. "c1" is always the first ground-truth Claim (crm_context runs first in
every qualifier), so the scripted fact-check attributes draft assertions to it.

The product-led signal qualifiers share two research shapes, so their cases are
generated: the account-first signals reuse one world + script (differing only in
the judge step keyed by lead_type), and the prospect signals reuse another.
"""

from __future__ import annotations

import json

from shared.conformance import ConformanceCase
from shared.model import FakeModel
from shared.tools.fetchers import World

# Which lead_type uses which shape. Kept in sync with the qualifier classes.
ACCOUNT_FIRST_LEAD_TYPES = [
    "big_fish", "mrr_fit", "spend_spike", "startup_rolloff", "new_customer",
    "unmanaged_ticket", "scale_activation", "plg_unclassified",
]
PROSPECT_LEAD_TYPES = [
    "recent_fundraise", "eng_headcount_growth", "job_switcher", "trust_center_nda",
    "lookalike",  # lookalike has no usage but the same person+company shape
]


def _grounded_factcheck() -> str:
    # the faithful drafter only asserts grounded facts; attribute to c1
    return json.dumps([{"assertion": "a grounded fact", "claim_ref": "c1"}])


# --- inbound --------------------------------------------------------------


def _inbound_world() -> World:
    return World(
        tasks={
            "t_in": {
                "task_id": "t_in",
                "category": "inbound",
                "inbound_message": "our funnels keep breaking, can PostHog help?",
                "lead": {"name": "Sam Rivera", "email": "sam@acme.com",
                         "title": "Head of Product", "company": "Acme",
                         "domain": "acme.com", "lead_source": "Contact sales form"},
            }
        },
        persons={"sam@acme.com": {"name": "Sam Rivera", "title": "Head of Product"}},
        companies={"acme.com": {"industry": "saas", "employees": 300}},
        usage={},
    )


def _inbound_research_script() -> dict[str, str]:
    return {
        "person_research.synthesis:sam@acme.com": json.dumps(
            [{"field": "seniority", "value": "Head of Product", "raw_keys": ["title"],
              "confidence": 0.9}]),
        "company_research.synthesis:acme.com": json.dumps(
            [{"field": "icp_industry_fit", "value": "strong", "raw_keys": ["industry"],
              "confidence": 0.8}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "debug broken funnels", "product": "analytics",
              "owner_persona": "Head of Product", "raw_keys": ["message"], "confidence": 0.85}]),
    }


def inbound_cases() -> list[ConformanceCase]:
    call_model = FakeModel({
        **_inbound_research_script(),
        "inbound.judgment": json.dumps(
            {"disposition": "call", "reasoning": "clear use case (c1)", "confidence": 0.82,
             "claim_refs": ["c1"], "target_email": "sam@acme.com"}),
        "drafter": json.dumps({"subject": "PostHog at Acme",
                               "body": "Saw your funnels keep breaking.", "claims_used": ["c1"]}),
        "factcheck": _grounded_factcheck(),
    })
    nurture_model = FakeModel({
        **_inbound_research_script(),
        "inbound.judgment": json.dumps(
            {"disposition": "nurture", "reasoning": "not ready (c1)", "confidence": 0.5,
             "claim_refs": ["c1"], "target_email": "sam@acme.com"}),
    })
    return [
        ConformanceCase("inbound/call", _inbound_world(), "t_in", call_model),
        ConformanceCase("inbound/nurture", _inbound_world(), "t_in", nurture_model),
    ]


# --- product-led: account-first signals -----------------------------------


def _plg_world() -> World:
    return World(
        tasks={
            "t_plg": {
                "task_id": "t_plg",
                "category": "product-led",
                "lead": {"name": "Sam Rivera", "email": "sam@acme.com", "title": "Engineer",
                         "company": "Acme", "domain": "acme.com", "lead_source": "Product-led"},
                "account_ref": "acct_acme",
            }
        },
        persons={
            "sam@acme.com": {"name": "Sam Rivera", "title": "Engineer"},
            "dana@acme.com": {"name": "Dana Lopez", "title": "VP Engineering"},
        },
        companies={"acme.com": {"industry": "devtools", "employees": 900}},
        usage={
            "acct_acme": {
                "events_30d": 6_000_000, "products": ["analytics", "replay"],
                "roster": [
                    {"email": "sam@acme.com", "name": "Sam Rivera", "touches": ["analytics"]},
                    {"email": "dana@acme.com", "name": "Dana Lopez", "touches": ["replay"]},
                ],
            }
        },
    )


def _plg_research_script() -> dict[str, str]:
    return {
        "usage_research.synthesis:acct_acme": json.dumps(
            [{"field": "monthly_event_volume", "value": 6_000_000, "raw_keys": ["events_30d"],
              "confidence": 0.99}]),
        "person_research.synthesis:sam@acme.com": json.dumps(
            [{"field": "seniority", "value": "IC", "raw_keys": ["title"], "confidence": 0.9}]),
        "person_research.synthesis:dana@acme.com": json.dumps(
            [{"field": "seniority", "value": "VP Engineering", "raw_keys": ["title"],
              "confidence": 0.95}]),
        "company_research.synthesis:acme.com": json.dumps(
            [{"field": "segment", "value": "mid-market", "raw_keys": ["employees"],
              "confidence": 0.8}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "safe gradual rollout", "product": "flags",
              "owner_persona": "VP Engineering", "raw_keys": ["usage"], "confidence": 0.8}]),
    }


def account_first_cases(lead_type: str) -> list[ConformanceCase]:
    model = FakeModel({
        **_plg_research_script(),
        f"{lead_type}.judgment": json.dumps(
            {"disposition": "call", "reasoning": "usage clears bar (c1); engage VP",
             "confidence": 0.8, "claim_refs": ["c1"], "target_email": "dana@acme.com"}),
        "drafter": json.dumps({"subject": "PostHog at Acme",
                               "body": "Your team is at ~6M events/mo.", "claims_used": ["c1"]}),
        "factcheck": _grounded_factcheck(),
    })
    return [ConformanceCase(f"{lead_type}/call", _plg_world(), "t_plg", model)]


# Agentic account-first: same world + complete-script, plus a scripted run_turn
# turn-list. The agent calls the same tools the deterministic gather did, in the
# same order, so the same Claims land (c1 = crm, seeded before the loop).
AGENTIC_ACCOUNT_FIRST_TURNS = [
    {"tool_calls": [{"name": "usage_research", "input": {}}]},
    {"tool_calls": [{"name": "person_research", "input": {"email": "sam@acme.com", "name": "Sam Rivera"}},
                    {"name": "person_research", "input": {"email": "dana@acme.com", "name": "Dana Lopez"}}]},
    {"tool_calls": [{"name": "company_research", "input": {"domain": "acme.com"}}]},
    {"tool_calls": [{"name": "use_case_mapping", "input": {}}]},
    {"text": "Gathered usage, both personas, company, and the use case. Ready to judge.",
     "stop": "end_turn"},
]


def agentic_account_first_cases(lead_type: str) -> list[ConformanceCase]:
    model = FakeModel({
        **_plg_research_script(),
        f"{lead_type}.judgment": json.dumps(
            {"disposition": "call", "reasoning": "usage clears bar (c1); engage VP",
             "confidence": 0.8, "claim_refs": ["c1"], "target_email": "dana@acme.com"}),
        "drafter": json.dumps({"subject": "PostHog at Acme",
                               "body": "Your team is at ~6M events/mo.", "claims_used": ["c1"]}),
        "factcheck": _grounded_factcheck(),
    })
    model.set_tools(f"{lead_type}.agent", AGENTIC_ACCOUNT_FIRST_TURNS)
    return [ConformanceCase(f"{lead_type}/call", _plg_world(), "t_plg", model)]


# --- product-led: prospect signals ----------------------------------------


def _prospect_world() -> World:
    return World(
        tasks={
            "t_pro": {
                "task_id": "t_pro",
                "category": "product-led",
                "lead": {"name": "Jo Kim", "email": "jo@beta.io", "title": "VP Engineering",
                         "company": "Beta", "domain": "beta.io", "lead_source": "Product-led"},
            }
        },
        persons={"jo@beta.io": {"name": "Jo Kim", "title": "VP Engineering"}},
        companies={"beta.io": {"industry": "fintech", "employees": 200}},
        usage={},
    )


def _prospect_research_script() -> dict[str, str]:
    return {
        "person_research.synthesis:jo@beta.io": json.dumps(
            [{"field": "seniority", "value": "VP Engineering", "raw_keys": ["title"],
              "confidence": 0.9}]),
        "company_research.synthesis:beta.io": json.dumps(
            [{"field": "segment", "value": "growth-stage fintech", "raw_keys": ["industry"],
              "confidence": 0.8}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "stand up product analytics as they scale", "product": "analytics",
              "owner_persona": "VP Engineering", "raw_keys": ["company"], "confidence": 0.75}]),
    }


# Agentic prospect: the agent enriches the person + company and maps the use case
# (no usage; the prospect world has no account). Same complete-script as the
# deterministic prospect flow, plus a scripted run_turn turn-list.
AGENTIC_PROSPECT_TURNS = [
    {"tool_calls": [{"name": "person_research", "input": {"email": "jo@beta.io", "name": "Jo Kim"}}]},
    {"tool_calls": [{"name": "company_research", "input": {"domain": "beta.io"}}]},
    {"tool_calls": [{"name": "use_case_mapping", "input": {}}]},
    {"text": "Enriched the persona and company and mapped the use case. Ready to judge.",
     "stop": "end_turn"},
]


def agentic_prospect_cases(lead_type: str) -> list[ConformanceCase]:
    model = FakeModel({
        **_prospect_research_script(),
        f"{lead_type}.judgment": json.dumps(
            {"disposition": "call", "reasoning": "strong fit and timing (c1)",
             "confidence": 0.72, "claim_refs": ["c1"], "target_email": "jo@beta.io"}),
        "drafter": json.dumps({"subject": "PostHog for Beta",
                               "body": "Saw Beta is scaling fast.", "claims_used": ["c1"]}),
        "factcheck": _grounded_factcheck(),
    })
    model.set_tools(f"{lead_type}.agent", AGENTIC_PROSPECT_TURNS)
    return [ConformanceCase(f"{lead_type}/call", _prospect_world(), "t_pro", model)]


def prospect_cases(lead_type: str) -> list[ConformanceCase]:
    model = FakeModel({
        **_prospect_research_script(),
        f"{lead_type}.judgment": json.dumps(
            {"disposition": "call", "reasoning": "strong fit and timing (c1)",
             "confidence": 0.72, "claim_refs": ["c1"], "target_email": "jo@beta.io"}),
        "drafter": json.dumps({"subject": "PostHog for Beta",
                               "body": "Saw Beta is scaling fast.", "claims_used": ["c1"]}),
        "factcheck": _grounded_factcheck(),
    })
    return [ConformanceCase(f"{lead_type}/call", _prospect_world(), "t_pro", model)]


# --- outbound -------------------------------------------------------------


def _outbound_world() -> World:
    return World(
        tasks={
            "t_out": {
                "task_id": "t_out",
                "category": "outbound",
                "lead": {"name": "Lee Park", "email": "lee@gamma.dev", "title": "CTO",
                         "company": "Gamma", "domain": "gamma.dev",
                         "lead_source": "lemlist", "active_sequence": False},
            }
        },
        persons={"lee@gamma.dev": {"name": "Lee Park", "title": "CTO"}},
        companies={"gamma.dev": {"industry": "devtools", "employees": 80}},
        usage={},
    )


AGENTIC_OUTBOUND_TURNS = [
    {"tool_calls": [{"name": "person_research", "input": {"email": "lee@gamma.dev", "name": "Lee Park"}}]},
    {"tool_calls": [{"name": "company_research", "input": {"domain": "gamma.dev"}}]},
    {"tool_calls": [{"name": "use_case_mapping", "input": {}}]},
    {"text": "Enriched the prospect and company and mapped the use case. Ready to judge.",
     "stop": "end_turn"},
]


def outbound_cases() -> list[ConformanceCase]:
    model = FakeModel({
        "person_research.synthesis:lee@gamma.dev": json.dumps(
            [{"field": "seniority", "value": "CTO", "raw_keys": ["title"], "confidence": 0.9}]),
        "company_research.synthesis:gamma.dev": json.dumps(
            [{"field": "segment", "value": "early-stage devtools", "raw_keys": ["industry"],
              "confidence": 0.8}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "instrument their product as they scale", "product": "analytics",
              "owner_persona": "CTO", "raw_keys": ["company"], "confidence": 0.75}]),
        "outbound.judgment": json.dumps(
            {"disposition": "call", "reasoning": "strong founder-led fit (c1)",
             "confidence": 0.7, "claim_refs": ["c1"], "target_email": "lee@gamma.dev"}),
        "drafter": json.dumps({"subject": "PostHog for Gamma",
                               "body": "Saw Gamma is building devtools.", "claims_used": ["c1"]}),
        "factcheck": _grounded_factcheck(),
    })
    model.set_tools("outbound.agent", AGENTIC_OUTBOUND_TURNS)
    return [ConformanceCase("outbound/call", _outbound_world(), "t_out", model)]


# --- onboarding -----------------------------------------------------------


def _onboarding_world() -> World:
    return World(
        tasks={
            "t_ob": {
                "task_id": "t_ob",
                "category": "onboarding",
                "lead": {"name": "Jo Kim", "email": "jo@beta.io", "title": "Eng Manager",
                         "company": "Beta", "domain": "beta.io", "lead_source": "Onboarding referral"},
                "account_ref": "acct_beta",
            }
        },
        persons={"jo@beta.io": {"name": "Jo Kim", "title": "Eng Manager"}},
        companies={"beta.io": {"industry": "fintech"}},
        usage={"acct_beta": {"events_30d": 120_000, "products": ["analytics"],
                             "activation": ["installed_sdk"], "roster": []}},
    )


def onboarding_cases() -> list[ConformanceCase]:
    model = FakeModel({
        "person_research.synthesis:jo@beta.io": json.dumps(
            [{"field": "role", "value": "Eng Manager", "raw_keys": ["title"], "confidence": 0.9}]),
        "usage_research.synthesis:acct_beta": json.dumps(
            [{"field": "activation_signals", "value": "installed SDK, no dashboards yet",
              "raw_keys": ["activation"], "confidence": 0.85}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "stand up product analytics", "product": "analytics",
              "owner_persona": "Eng Manager", "raw_keys": ["usage"], "confidence": 0.8}]),
        "onboarding.judgment": json.dumps(
            {"disposition": "call", "reasoning": "early activation, help unblock (c1)",
             "confidence": 0.75, "claim_refs": ["c1"], "target_email": "jo@beta.io"}),
        "drafter": json.dumps({"subject": "Getting started with PostHog",
                               "body": "Saw you installed the SDK.", "claims_used": ["c1"]}),
        "factcheck": _grounded_factcheck(),
    })
    return [ConformanceCase("onboarding/call", _onboarding_world(), "t_ob", model)]


def all_cases() -> dict[str, list[ConformanceCase]]:
    cases: dict[str, list[ConformanceCase]] = {
        "inbound": inbound_cases(),
        "onboarding": onboarding_cases(),
        "outbound": outbound_cases(),
    }
    for lt in ACCOUNT_FIRST_LEAD_TYPES:
        # the whole account-first family is agentic as of Phase 3.
        cases[lt] = agentic_account_first_cases(lt)
    for lt in PROSPECT_LEAD_TYPES:
        # the prospect family is agentic as of Phase 3.
        cases[lt] = agentic_prospect_cases(lt)
    return cases
