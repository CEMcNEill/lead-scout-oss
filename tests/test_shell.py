"""Shell tests: process_lead_run across the main paths.

Happy path stages a clean draft and writes the ledger; a hard-stop blocks before
any spend; the boundary gate strips an ungrounded draft; a runaway interior is
hard-stopped by the per-run cost cap.
"""

import json
from pathlib import Path

import pytest

from engine.cost import BudgetGovernor, ModelPolicy, Pricing
from engine.hardstops import HardStopConfig
from engine.ledger import Ledger
from engine.providers import FixedClock, NullStagingSink, StubToolProvider
from engine.router import Router
from engine.shell import Shell
from shared.contracts import RepConfig, RunStatus, TriggerMeta, TriggerSource
from shared.model import FakeModel
from shared.registry import build_default_registry
from shared.tools.fetchers import World

REPO = Path(__file__).resolve().parent.parent
RUBRIC = "Holistic. No single axis disqualifies."

REP = RepConfig(
    rep_id="rep_chris", sf_user_id="005x", sf_credential_ref="keychain:sf",
    gmail_account="chris.m@posthog.com", voice_profile_ref="voice/chris.md",
    signature="Chris", slack_post_target="U123", budget_cap_usd=50.0,
)


def _inbound_world(domain="acme.com") -> World:
    return World(
        tasks={
            "t_in": {
                "task_id": "t_in", "trigger": "demo_request",
                "inbound_message": "our funnels keep breaking, can PostHog help?",
                "lead": {"name": "Sam Rivera", "email": f"sam@{domain}",
                         "title": "Head of Product", "company": "Acme",
                         "domain": domain, "lead_source": "demo_request"},
            }
        },
        persons={f"sam@{domain}": {"name": "Sam Rivera", "title": "Head of Product"}},
        companies={domain: {"industry": "saas", "employees": 300}},
        usage={},
    )


def _research_script(domain="acme.com") -> dict[str, str]:
    return {
        f"person_research.synthesis:sam@{domain}": json.dumps(
            [{"field": "seniority", "value": "Head of Product", "raw_keys": ["title"],
              "confidence": 0.9}]),
        f"company_research.synthesis:{domain}": json.dumps(
            [{"field": "icp_industry_fit", "value": "strong", "raw_keys": ["industry"],
              "confidence": 0.8}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "debug broken funnels", "product": "analytics",
              "owner_persona": "Head of Product", "raw_keys": ["message"], "confidence": 0.85}]),
    }


def _build_shell(world, model, *, ledger, governor=None):
    policy = ModelPolicy()
    governor = governor or BudgetGovernor(policy, per_run_cap_usd=5.0, per_day_cap_usd=1000.0)
    return Shell(
        ledger=ledger,
        router=Router.from_yaml(REPO / "qualifiers" / "registry.yaml"),
        registry=build_default_registry(RUBRIC),
        hard_stops=HardStopConfig.from_yaml(REPO / "config" / "hard_stops.yaml"),
        governor=governor,
        inner_model=model,
        tool_provider=StubToolProvider(world, voice_profile="Plain prose.",
                                       exemplar_bank={"inbound": ["Hey, quick one..."]}),
        staging_sink=NullStagingSink(),
        clock=FixedClock(),
    )


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


def test_happy_path_stages_draft_and_writes_ledger(ledger):
    model = FakeModel({
        **_research_script(),
        "inbound.judgment": json.dumps(
            {"disposition": "call", "reasoning": "clear use case (c1)", "confidence": 0.82,
             "claim_refs": ["c1"], "target_email": "sam@acme.com"}),
        "drafter": json.dumps({"subject": "PostHog at Acme",
                               "body": "Saw your funnels keep breaking.", "claims_used": ["c1"]}),
        "factcheck": json.dumps([{"assertion": "funnels keep breaking", "claim_ref": "c1"}]),
    })
    shell = _build_shell(_inbound_world(), model, ledger=ledger)
    run = shell.process_lead_run("t_in", REP, TriggerMeta(TriggerSource.BATCH, ""))

    assert run.status == RunStatus.STAGED_FOR_REVIEW
    assert run.route.qualifier == "inbound"
    assert run.staged_draft is not None and run.staged_draft.to == "sam@acme.com"
    assert run.factcheck_flags == []
    assert run.model_policy_version == "2026-06-mvp"
    # persisted and deduppable
    assert ledger.has_task("t_in")
    assert ledger.get(run.id).staged_draft.subject == "PostHog at Acme"


def test_hard_stop_blocks_before_spend(ledger):
    # competitor domain -> blocked before any model call
    shell = _build_shell(_inbound_world("mixpanel.com"), FakeModel(), ledger=ledger)
    run = shell.process_lead_run("t_in", REP, TriggerMeta(TriggerSource.BATCH, ""))

    assert run.status == RunStatus.BLOCKED
    assert "competitor" in run.hard_stops
    assert run.staged_draft is None
    assert run.cost.total_usd == 0.0
    assert run.disposition is None


def test_factcheck_strips_ungrounded_draft(ledger):
    model = FakeModel({
        **_research_script(),
        "inbound.judgment": json.dumps(
            {"disposition": "call", "reasoning": "clear use case (c1)", "confidence": 0.82,
             "claim_refs": ["c1"], "target_email": "sam@acme.com"}),
        "drafter": json.dumps({"subject": "x", "body": "You just raised a Series C.",
                               "claims_used": []}),
        "factcheck": json.dumps([{"assertion": "You just raised a Series C.", "claim_ref": None}]),
    })
    shell = _build_shell(_inbound_world(), model, ledger=ledger)
    run = shell.process_lead_run("t_in", REP, TriggerMeta(TriggerSource.MANUAL, ""))

    # disposition still goes to review, but the unsafe draft is stripped
    assert run.status == RunStatus.STAGED_FOR_REVIEW
    assert run.staged_draft is None
    assert run.disposition is not None
    assert any("Series C" in f for f in run.factcheck_flags)


def test_runaway_interior_hard_stopped_by_per_run_cap(ledger):
    policy = ModelPolicy()
    policy.pricing["fake-model"] = Pricing(input_per_mtok=1000.0, output_per_mtok=1000.0)
    governor = BudgetGovernor(policy, per_run_cap_usd=0.0, per_day_cap_usd=1000.0)
    model = FakeModel(_research_script())  # first synthesis call already overspends
    shell = _build_shell(_inbound_world(), model, ledger=ledger, governor=governor)
    run = shell.process_lead_run("t_in", REP, TriggerMeta(TriggerSource.BATCH, ""))

    assert run.status == RunStatus.BLOCKED
    assert "per_run_budget_exceeded" in run.hard_stops
    assert run.cost.total_usd > 0
