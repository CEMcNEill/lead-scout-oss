"""End-to-end on fixtures: the whole fast loop, real production wiring, scripted
model.

Builds the shell exactly as the service does (assemble_shell), points it at
fixtures/world.json, and sweeps all five leads through the four qualifiers plus
the two hard-stop cases. Proves disposition routing, account-first targeting, the
grounding gate, dedup, cost metering, and version stamping all work together.

The model is scripted (FakeModel) so the run is deterministic; swapping in
AnthropicModel is the only change for a live run.
"""

import json
from pathlib import Path

import pytest

from engine.adapters import BatchAdapter, StubTaskSource
from engine.bootstrap import (
    ContactedTask,
    SentMessage,
    StubContactedTaskSource,
    StubSentMailReader,
    build_exemplar_bank,
)
from engine.ledger import Ledger
from engine.service import assemble_shell
from shared.contracts import RepConfig, RunStatus
from shared.model import FakeModel
from shared.tools.fetchers import World

REPO = Path(__file__).resolve().parent.parent
REP = RepConfig(
    rep_id="rep_chris", sf_user_id="005x", sf_credential_ref="kc",
    gmail_account="chris.m@posthog.com", voice_profile_ref="config/voice/chris.md",
    signature="Chris", slack_post_target="U1", budget_cap_usd=50.0,
)


def _use_case_resp(_system: str, prompt: str) -> str:
    # ground the use case in whichever evidence key this qualifier passed
    key = "message" if '"message":' in prompt else "usage"
    return json.dumps([{
        "use_case": "debug broken funnels", "product": "analytics",
        "owner_persona": "decision maker", "raw_keys": [key], "confidence": 0.8,
    }])


def _person(value: str) -> str:
    return json.dumps([{"field": "seniority", "value": value, "raw_keys": ["title"],
                        "confidence": 0.9}])


def _judgment(target_email: str) -> str:
    return json.dumps({"disposition": "call", "reasoning": "clear fit (c1)",
                       "confidence": 0.8, "claim_refs": ["c1"], "target_email": target_email})


def _model() -> FakeModel:
    from tests.fixtures.conformance_cases import AGENTIC_ACCOUNT_FIRST_TURNS

    model = FakeModel({
        # research synthesis, per-entity step names
        "person_research.synthesis:sam@acme.com": _person("IC engineer"),
        "person_research.synthesis:dana@acme.com": _person("VP Engineering"),
        "person_research.synthesis:jo@beta.io": _person("Engineering Manager"),
        "company_research.synthesis:acme.com": json.dumps(
            [{"field": "segment", "value": "mid-market", "raw_keys": ["industry"], "confidence": 0.8}]),
        "company_research.synthesis:beta.io": json.dumps(
            [{"field": "segment", "value": "smb", "raw_keys": ["industry"], "confidence": 0.8}]),
        "usage_research.synthesis:acct_acme": json.dumps(
            [{"field": "monthly_event_volume", "value": 6_200_000, "raw_keys": ["events_30d"],
              "confidence": 0.99}]),
        "usage_research.synthesis:acct_beta": json.dumps(
            [{"field": "activation_signals", "value": "installed SDK", "raw_keys": ["events_30d"],
              "confidence": 0.8}]),
        "use_case_mapping.synthesis": _use_case_resp,
        # judgments, per lead type
        "inbound.judgment": _judgment("sam@acme.com"),
        "big_fish.judgment": _judgment("dana@acme.com"),
        "onboarding.judgment": _judgment("jo@beta.io"),
        # drafting + gate (shared steps); empty assertion list -> gate passes
        "drafter": json.dumps({"subject": "PostHog", "body": "Short, grounded note.",
                               "claims_used": ["c1"]}),
        "factcheck": "[]",
    })
    # big_fish is agentic (Phase 3): script its research loop.
    model.set_tools("big_fish.agent", AGENTIC_ACCOUNT_FIRST_TURNS)
    return model


def _exemplar_bank() -> dict[str, list[str]]:
    tasks = [ContactedTask("h1", "inbound", "prior@lead.com", "Prior", "in_progress")]
    mail = {"prior@lead.com": [SentMessage("prior@lead.com", "Re: funnels",
                                           "Loved chatting about your funnels.", "2026-05-01")]}
    return build_exemplar_bank(StubContactedTaskSource(tasks), StubSentMailReader(mail), REP)


@pytest.fixture
def shell(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    world = World.load(REPO / "fixtures" / "world.json")
    sh = assemble_shell(
        ledger=ledger, inner_model=_model(), world=world,
        staging_dir=tmp_path / "staged", exemplar_bank=_exemplar_bank(),
    )
    yield sh
    ledger.close()


def test_full_sweep_over_fixtures(shell, tmp_path):
    world = World.load(REPO / "fixtures" / "world.json")
    batch = BatchAdapter(shell, StubTaskSource.from_world(world))
    result = batch.sweep(REP)

    by_task = {r.task_id: r for r in result.processed}
    assert set(by_task) == {"t_inbound", "t_plg", "t_onboard", "t_competitor", "t_personal"}

    # three qualified calls, each staged with a clean draft
    for tid, qualifier, target in [
        ("t_inbound", "inbound", "sam@acme.com"),
        ("t_plg", "big_fish", "dana@acme.com"),
        ("t_onboard", "onboarding", "jo@beta.io"),
    ]:
        run = by_task[tid]
        assert run.status == RunStatus.STAGED_FOR_REVIEW, tid
        assert run.route.qualifier == qualifier
        assert run.staged_draft is not None and run.staged_draft.to == target
        assert run.factcheck_flags == []  # zero ungrounded facts reached a draft
        assert run.disposition.claim_refs  # reasoning references real Claims
        assert run.cost.entries  # model calls were metered
        assert run.model_policy_version == "2026-06-mvp"
        assert run.voice_profile_version == "v1"
        assert run.rubric_version == "v1"

    # the product-led target is the discovered VP, not the named IC signup
    assert by_task["t_plg"].disposition.target.is_named_lead is False

    # two hard-stops, blocked with no spend and no draft
    assert by_task["t_competitor"].status == RunStatus.BLOCKED
    assert "competitor" in by_task["t_competitor"].hard_stops
    assert by_task["t_personal"].status == RunStatus.BLOCKED
    assert "personal_address" in by_task["t_personal"].hard_stops
    for tid in ("t_competitor", "t_personal"):
        assert by_task[tid].staged_draft is None
        assert by_task[tid].cost.total_usd == 0.0

    # staged drafts were written to the sink
    staged_files = list((tmp_path / "staged").glob("*.json"))
    assert len(staged_files) == 3


def test_second_sweep_is_fully_deduped(shell):
    world = World.load(REPO / "fixtures" / "world.json")
    batch = BatchAdapter(shell, StubTaskSource.from_world(world))
    batch.sweep(REP)
    second = batch.sweep(REP)
    assert second.processed == []
    assert len(second.skipped) == 5
    assert shell.ledger.count() == 5


def test_dossier_provenance_is_complete(shell):
    """Every Claim in every staged dossier carries provenance (raw)."""
    world = World.load(REPO / "fixtures" / "world.json")
    BatchAdapter(shell, StubTaskSource.from_world(world)).sweep(REP)
    for run in shell.ledger.list_runs(status="staged_for_review"):
        assert run.dossier
        for claim in run.dossier:
            assert claim.raw not in (None, {}, [], ""), (run.task_id, claim.field)
            assert claim.source
