"""Qualifier interior tests: gather -> judge -> draft, on scripted models.

Covers the inbound flow (named lead is the target) and the product-led flow
(target is discovered from the roster and may differ from the named lead).
"""

import json

from qualifiers.big_fish.qualifier import BigFishQualifier
from qualifiers.inbound.qualifier import InboundQualifier
from shared.contracts import DispositionKind
from shared.model import FakeModel
from shared.tools.fetchers import (
    StubCompanyFetcher,
    StubCrmFetcher,
    StubPersonFetcher,
    StubUsageFetcher,
    World,
)
from shared.tools.toolbox import build_toolbox

RUBRIC = "Holistic. No single axis disqualifies."


def _toolbox(world: World, model: FakeModel):
    return build_toolbox(
        crm_fetcher=StubCrmFetcher(world),
        person_fetcher=StubPersonFetcher(world),
        company_fetcher=StubCompanyFetcher(world),
        usage_fetcher=StubUsageFetcher(world),
        model=model,
        voice_profile="Plain prose, no emdashes.",
        exemplars=["Hey, quick one about your funnels..."],
        signature="Chris",
    )


def test_inbound_qualifier_calls_and_drafts_to_named_lead():
    world = World(
        tasks={
            "t1": {
                "task_id": "t1",
                "trigger": "demo_request",
                "inbound_message": "our funnels keep breaking, can PostHog help?",
                "lead": {"name": "Sam Rivera", "email": "sam@acme.com",
                         "title": "Head of Product", "company": "Acme", "domain": "acme.com",
                         "lead_source": "demo_request"},
            }
        },
        persons={"sam@acme.com": {"name": "Sam Rivera", "title": "Head of Product"}},
        companies={"acme.com": {"industry": "saas", "employees": 300}},
        usage={},
    )
    model = FakeModel({
        "person_research.synthesis:sam@acme.com": json.dumps(
            [{"field": "seniority", "value": "Head of Product", "raw_keys": ["title"],
              "confidence": 0.9}]),
        "company_research.synthesis:acme.com": json.dumps(
            [{"field": "icp_industry_fit", "value": "strong", "raw_keys": ["industry"],
              "confidence": 0.8}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "debug broken funnels", "product": "analytics",
              "owner_persona": "Head of Product", "raw_keys": ["message"], "confidence": 0.85}]),
        "inbound.judgment": json.dumps(
            {"disposition": "call", "reasoning": "clear use case (c1) and a senior persona",
             "confidence": 0.82, "claim_refs": ["c1"], "target_email": "sam@acme.com"}),
        "drafter": json.dumps(
            {"subject": "PostHog for funnel debugging at Acme",
             "body": "Saw your funnels keep breaking.", "claims_used": ["c1"]}),
    })
    q = InboundQualifier(RUBRIC)
    result = q.run("t1", world.tasks["t1"], _toolbox(world, model))

    assert result.disposition.disposition == DispositionKind.CALL
    assert result.draft is not None
    assert result.draft.to == "sam@acme.com"
    assert result.disposition.target.is_named_lead is True
    # dossier carries grounded provenance throughout
    assert all(c.raw for c in result.dossier)
    assert result.disposition.claim_refs == ["c1"]


def test_plg_big_fish_targets_discovered_buyer_not_named_lead():
    world = World(
        tasks={
            "t2": {
                "task_id": "t2",
                "category": "product-led",
                "signal": "big_fish",
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
                "events_30d": 6_000_000,
                "products": ["analytics", "replay"],
                "roster": [
                    {"email": "sam@acme.com", "name": "Sam Rivera", "touches": ["analytics"]},
                    {"email": "dana@acme.com", "name": "Dana Lopez", "touches": ["replay"]},
                ],
            }
        },
    )
    model = FakeModel({
        "usage_research.synthesis:acct_acme": json.dumps(
            [{"field": "monthly_event_volume", "value": 6_000_000, "raw_keys": ["events_30d"],
              "confidence": 0.99},
             {"field": "trajectory", "value": "spiking", "raw_keys": ["events_30d"],
              "confidence": 0.8}]),
        "person_research.synthesis:sam@acme.com": json.dumps(
            [{"field": "seniority", "value": "IC engineer", "raw_keys": ["title"], "confidence": 0.9}]),
        "person_research.synthesis:dana@acme.com": json.dumps(
            [{"field": "seniority", "value": "VP Engineering", "raw_keys": ["title"],
              "confidence": 0.95},
             {"field": "budget_ownership", "value": True, "raw_keys": ["title"], "confidence": 0.7}]),
        "company_research.synthesis:acme.com": json.dumps(
            [{"field": "segment", "value": "mid-market", "raw_keys": ["employees"], "confidence": 0.8}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "safe gradual rollout", "product": "flags",
              "owner_persona": "VP Engineering", "raw_keys": ["usage"], "confidence": 0.8}]),
        "big_fish.judgment": json.dumps(
            {"disposition": "call",
             "reasoning": "explosive usage clears the bar; engage the VP, not the IC signup",
             "confidence": 0.8, "claim_refs": ["c1"], "target_email": "dana@acme.com"}),
        "drafter": json.dumps(
            {"subject": "PostHog at Acme", "body": "Your team is at ~6M events/mo.",
             "claims_used": ["c1"]}),
    })
    # big_fish is agentic (Phase 3): the interior orchestrates the research tools.
    from tests.fixtures.conformance_cases import AGENTIC_ACCOUNT_FIRST_TURNS

    model.set_tools("big_fish.agent", AGENTIC_ACCOUNT_FIRST_TURNS)
    q = BigFishQualifier(RUBRIC)
    result = q.run("t2", world.tasks["t2"], _toolbox(world, model))

    assert result.disposition.disposition == DispositionKind.CALL
    target = result.disposition.target
    assert target.email == "dana@acme.com"
    assert target.is_named_lead is False
    assert result.draft.to == "dana@acme.com"


def test_lookalike_folds_in_usage_when_the_account_is_active():
    # a lead tagged "lookalike" can still be an active PostHog account; its real
    # usage must reach the dossier, not be skipped (a "lookalike" can be an account).
    from qualifiers.lookalike.qualifier import LookalikeQualifier

    world = World(
        tasks={
            "t_la": {
                "task_id": "t_la",
                "category": "product-led",
                "signal": "lookalike",
                "lead": {"name": "Sam Rivera", "email": "sam@acme.com", "title": None,
                         "company": "Acme", "domain": "acme.com", "lead_source": "Product-led"},
                "account_ref": "acct_acme",
            }
        },
        persons={"sam@acme.com": {"name": "Sam Rivera", "title": None}},
        companies={"acme.com": {"industry": "ai devtools", "employees": 101}},
        usage={"acct_acme": {"events_30d": 1_454_571, "products": ["feature_flags", "recordings"],
                              "paid_invoice_count": 2, "roster": []}},
    )
    model = FakeModel({
        "person_research.synthesis:sam@acme.com": "[]",
        "company_research.synthesis:acme.com": json.dumps(
            [{"field": "segment", "value": "ai devtools", "raw_keys": ["industry"], "confidence": 0.8}]),
        "usage_research.synthesis:acct_acme": json.dumps(
            [{"field": "monthly_event_volume", "value": 1_454_571, "raw_keys": ["events_30d"],
              "confidence": 0.99}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "product analytics", "product": "analytics",
              "owner_persona": None, "raw_keys": ["usage"], "confidence": 0.7}]),
        "lookalike.judgment": json.dumps(
            {"disposition": "call", "reasoning": "real usage (c1) - active account, not a cold lookalike",
             "confidence": 0.8, "claim_refs": ["c1"], "target_email": "sam@acme.com"}),
        "drafter": json.dumps({"subject": "PostHog at Acme",
                               "body": "Your team is at ~1.5M events/mo.", "claims_used": ["c1"]}),
        "factcheck": json.dumps([{"assertion": "a grounded fact", "claim_ref": "c1"}]),
    })
    # lookalike is agentic (Phase 3); when the lookalike resolves to an active
    # account the agent also pulls usage, so its turn-list includes usage_research.
    model.set_tools("lookalike.agent", [
        {"tool_calls": [{"name": "usage_research", "input": {}}]},
        {"tool_calls": [{"name": "person_research",
                         "input": {"email": "sam@acme.com", "name": "Sam Rivera"}}]},
        {"tool_calls": [{"name": "company_research", "input": {"domain": "acme.com"}}]},
        {"tool_calls": [{"name": "use_case_mapping", "input": {}}]},
        {"text": "Pulled usage, persona, company, use case.", "stop": "end_turn"},
    ])
    q = LookalikeQualifier(RUBRIC)
    result = q.run("t_la", world.tasks["t_la"], _toolbox(world, model))

    # the usage claim reached the dossier (the bug was that it never did)
    assert any(c.field == "monthly_event_volume" for c in result.dossier)
    assert result.disposition.disposition == DispositionKind.CALL


def test_disqualify_produces_no_draft():
    world = World(
        tasks={"t3": {"task_id": "t3", "trigger": "demo_request",
                      "inbound_message": "just browsing",
                      "lead": {"name": "Pat", "email": "pat@acme.com", "domain": "acme.com"}}},
        persons={"pat@acme.com": {"name": "Pat", "title": "Student"}},
        companies={"acme.com": {"industry": "edu"}},
        usage={},
    )
    model = FakeModel({
        "person_research.synthesis:pat@acme.com": "[]",
        "company_research.synthesis:acme.com": "[]",
        "use_case_mapping.synthesis": "[]",
        "inbound.judgment": json.dumps(
            {"disposition": "nurture", "reasoning": "no clear use case yet",
             "confidence": 0.5, "claim_refs": [], "target_email": "pat@acme.com"}),
    })
    q = InboundQualifier(RUBRIC)
    result = q.run("t3", world.tasks["t3"], _toolbox(world, model))
    assert result.disposition.disposition == DispositionKind.NURTURE
    assert result.draft is None
