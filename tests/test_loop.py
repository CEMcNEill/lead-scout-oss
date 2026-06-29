"""Fast-loop tests: dedup across sweeps, hard-stops in a sweep, budget halt, and
manual re-run idempotency."""

import json
from pathlib import Path

import pytest

from engine.adapters import BatchAdapter, ManualAdapter, StubTaskSource
from engine.cost import BudgetGovernor, ModelPolicy, Pricing
from engine.hardstops import HardStopConfig
from engine.ledger import Ledger
from engine.providers import FixedClock, NullStagingSink, StubToolProvider
from engine.router import Router
from engine.shell import Shell
from shared.contracts import RepConfig, RunStatus, TriggerSource
from shared.model import FakeModel
from shared.registry import build_default_registry
from shared.tools.fetchers import World

REPO = Path(__file__).resolve().parent.parent
RUBRIC = "Holistic."
REP = RepConfig(
    rep_id="rep_chris", sf_user_id="005x", sf_credential_ref="kc",
    gmail_account="chris.m@posthog.com", voice_profile_ref="v", signature="Chris",
    slack_post_target="U1", budget_cap_usd=50.0,
)


def _world() -> World:
    return World(
        tasks={
            "t_inbound": {
                "task_id": "t_inbound", "trigger": "demo_request",
                "inbound_message": "funnels keep breaking",
                "lead": {"name": "Sam", "email": "sam@acme.com", "title": "Head of Product",
                         "company": "Acme", "domain": "acme.com", "lead_source": "demo_request"},
            },
            "t_competitor": {
                "task_id": "t_competitor", "trigger": "demo_request",
                "inbound_message": "comparing",
                "lead": {"name": "Alex", "email": "alex@mixpanel.com", "domain": "mixpanel.com"},
            },
            "t_inbound2": {
                "task_id": "t_inbound2", "trigger": "demo_request",
                "inbound_message": "interested",
                "lead": {"name": "Robin", "email": "robin@gamma.dev", "title": "CTO",
                         "company": "Gamma", "domain": "gamma.dev", "lead_source": "demo_request"},
            },
        },
        persons={"sam@acme.com": {"name": "Sam", "title": "Head of Product"}},
        companies={"acme.com": {"industry": "saas"}},
        usage={},
    )


def _model() -> FakeModel:
    return FakeModel({
        "person_research.synthesis:sam@acme.com": json.dumps(
            [{"field": "seniority", "value": "Head of Product", "raw_keys": ["title"], "confidence": 0.9}]),
        "company_research.synthesis:acme.com": json.dumps(
            [{"field": "icp_industry_fit", "value": "strong", "raw_keys": ["industry"], "confidence": 0.8}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "debug funnels", "product": "analytics", "owner_persona": "PM",
              "raw_keys": ["message"], "confidence": 0.85}]),
        "inbound.judgment": json.dumps(
            {"disposition": "call", "reasoning": "use case (c1)", "confidence": 0.8,
             "claim_refs": ["c1"], "target_email": "sam@acme.com"}),
        "drafter": json.dumps({"subject": "PostHog", "body": "funnels", "claims_used": ["c1"]}),
        "factcheck": json.dumps([{"assertion": "funnels", "claim_ref": "c1"}]),
    })


def _shell(ledger, model, governor=None) -> Shell:
    policy = ModelPolicy()
    return Shell(
        ledger=ledger,
        router=Router.from_yaml(REPO / "qualifiers" / "registry.yaml"),
        registry=build_default_registry(RUBRIC),
        hard_stops=HardStopConfig.from_yaml(REPO / "config" / "hard_stops.yaml"),
        governor=governor or BudgetGovernor(policy, per_run_cap_usd=5.0, per_day_cap_usd=1000.0),
        inner_model=model,
        tool_provider=StubToolProvider(_world(), voice_profile="Plain.",
                                       exemplar_bank={"inbound": []}),
        staging_sink=NullStagingSink(),
        clock=FixedClock(),
    )


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "l.db")
    yield led
    led.close()


def test_sweep_processes_and_dedups(ledger):
    batch = BatchAdapter(_shell(ledger, _model()),
                         StubTaskSource(["t_inbound", "t_competitor"]))

    first = batch.sweep(REP)
    assert len(first.processed) == 2
    assert first.staged == 1  # inbound staged
    assert first.skipped == []
    # the competitor was processed but blocked
    statuses = {r.task_id: r.status for r in first.processed}
    assert statuses["t_competitor"] == RunStatus.BLOCKED
    assert statuses["t_inbound"] == RunStatus.STAGED_FOR_REVIEW

    # second sweep: both already in the ledger -> all skipped, nothing processed
    second = batch.sweep(REP)
    assert second.processed == []
    assert set(second.skipped) == {"t_inbound", "t_competitor"}
    assert ledger.count() == 2


def test_sweep_halts_on_day_cap(ledger):
    policy = ModelPolicy()
    policy.pricing["fake-model"] = Pricing(input_per_mtok=1000.0, output_per_mtok=1000.0)
    # day cap tiny: the first run's spend pushes day_spent over, halting the next begin
    governor = BudgetGovernor(policy, per_run_cap_usd=100.0, per_day_cap_usd=0.0001)
    # first task spends past the day cap; the second (clean, non-hard-stop) task
    # reaches the budget gate and halts the sweep before starting
    batch = BatchAdapter(_shell(ledger, _model(), governor),
                         StubTaskSource(["t_inbound", "t_inbound2"]))
    result = batch.sweep(REP)
    assert result.halted
    assert "cap" in (result.halt_reason or "")
    assert len(result.processed) == 1  # only the first ran; nothing new started


def test_manual_rerun_is_idempotent(ledger):
    manual = ManualAdapter(_shell(ledger, _model()))
    run1 = manual.run("t_inbound", REP)
    run2 = manual.run("t_inbound", REP)
    assert run1.id != run2.id
    assert ledger.count() == 1  # one row per task; last run wins
    assert ledger.get_by_task("t_inbound").id == run2.id
