"""AgenticQualifier interior: grounding preserved, buying-group depth, loop
bounds, and the entity guard. Uses the conformance world + a scripted FakeModel
(complete steps for the real tools + a run_turn turn-list). No live calls."""

import json

import pytest

from qualifiers.startup_rolloff.qualifier import StartupRolloffQualifier
from shared.model import FakeModel
from shared.tools.fetchers import (
    StubCompanyFetcher, StubCrmFetcher, StubPersonFetcher, StubUsageFetcher,
)
from shared.tools.toolbox import build_toolbox
from tests.fixtures.conformance_cases import (
    AGENTIC_ACCOUNT_FIRST_TURNS, _plg_research_script, _plg_world,
)

RUBRIC = "Holistic. No single axis disqualifies."


def _toolbox(model):
    w = _plg_world()
    tb = build_toolbox(
        crm_fetcher=StubCrmFetcher(w), person_fetcher=StubPersonFetcher(w),
        company_fetcher=StubCompanyFetcher(w), usage_fetcher=StubUsageFetcher(w),
        model=model, voice_profile="Plain.", exemplars=[],
    )
    return tb, w.tasks["t_plg"]


def _model(turns=AGENTIC_ACCOUNT_FIRST_TURNS):
    m = FakeModel({
        **_plg_research_script(),
        "startup_rolloff.judgment": json.dumps(
            {"disposition": "call", "reasoning": "usage clears bar (c1); engage VP",
             "confidence": 0.8, "claim_refs": ["c1"], "target_email": "dana@acme.com"}),
        "drafter": json.dumps({"subject": "x", "body": "~6M events/mo.", "claims_used": ["c1"]}),
        "factcheck": json.dumps([{"assertion": "a", "claim_ref": "c1"}]),
    })
    m.set_tools("startup_rolloff.agent", turns)
    return m


def test_agentic_gather_grounds_every_claim_and_finds_buying_group():
    m = _model()
    tb, record = _toolbox(m)
    dossier, candidates = StartupRolloffQualifier(RUBRIC).gather("t_plg", record, tb)
    # depth: more than the c1 crm seed (the gate's "non-empty dossier" alone proves nothing)
    assert len(dossier) >= 4
    assert any(c.id != "c1" for c in dossier)
    # grounding/provenance preserved: the agent only orchestrates tools, never mints a Claim
    assert all(c.raw not in (None, {}, [], "") for c in dossier)
    # buying-group discovery surfaced the VP from the usage roster (not the named IC)
    assert any(c.get("email") == "dana@acme.com" for c in candidates)


def test_agentic_run_produces_a_grounded_draft_to_the_chosen_target():
    m = _model()
    tb, record = _toolbox(m)
    res = StartupRolloffQualifier(RUBRIC).run("t_plg", record, tb)
    assert res.disposition.disposition.value == "call"
    assert res.draft is not None and res.draft.to == "dana@acme.com"


def test_agentic_loop_terminates_at_max_tool_calls():
    never_stop = [{"tool_calls": [{"name": "company_research", "input": {"domain": "acme.com"}}]}
                  for _ in range(50)]
    m = _model(turns=never_stop)
    tb, record = _toolbox(m)
    q = StartupRolloffQualifier(RUBRIC)
    q.max_tool_calls = 3
    q.gather("t_plg", record, tb)
    run_turns = [c for c in m.calls if c.get("kind") == "run_turn"]
    assert len(run_turns) == 3  # the turn bound stopped the loop


def test_agentic_entity_guard_rejects_a_guessed_contact():
    turns = [
        {"tool_calls": [{"name": "person_research",
                         "input": {"email": "stranger@evil.com", "name": "Mallory"}}]},
        {"text": "done", "stop": "end_turn"},
    ]
    m = _model(turns=turns)
    tb, record = _toolbox(m)
    dossier, candidates = StartupRolloffQualifier(RUBRIC).gather("t_plg", record, tb)
    # the guessed email was rejected: no candidate for it, no person claim minted
    assert not any(c.get("email") == "stranger@evil.com" for c in candidates)
    assert all(c.source != "person_research" for c in dossier)  # nothing was enriched


def test_charter_sections_feed_judge_and_draft_guidance():
    q = StartupRolloffQualifier(RUBRIC)
    # the SKILL.md "How to draft" section is the live drafting guidance source
    assert "pre-paid" in q.draft_guidance.lower() or "volume discount" in q.draft_guidance.lower()
