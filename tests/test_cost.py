"""Cost layer tests: pricing, metering, per-run cap, per-day halt, kill switch."""

import pytest

from engine.cost import (
    BudgetGovernor,
    DayBudgetHalt,
    KillSwitchEngaged,
    MeteredModel,
    ModelPolicy,
    PerRunBudgetExceeded,
    Pricing,
)
from shared.contracts import Cost
from shared.model import FakeModel, ModelTier


def _policy_with_fake_pricing() -> ModelPolicy:
    policy = ModelPolicy()
    # price the fake model so metering is observable in tests
    policy.pricing["fake-model"] = Pricing(input_per_mtok=10.0, output_per_mtok=30.0)
    return policy


def test_policy_tier_mapping():
    policy = ModelPolicy()
    assert "opus" in policy.model_for(ModelTier.QUALIFIER_JUDGMENT)
    assert "sonnet" in policy.model_for(ModelTier.RESEARCH_SYNTHESIS)
    assert "haiku" in policy.model_for(ModelTier.ROUTING_FALLBACK)


def test_cost_usd_known_and_unknown_model():
    policy = ModelPolicy()
    # opus 4.8: 5 in / 25 out per Mtok
    assert policy.cost_usd("claude-opus-4-8", 1_000_000, 1_000_000) == 30.0
    # unknown model meters at zero
    assert policy.cost_usd("mystery", 1_000_000, 1_000_000) == 0.0


def test_metered_model_records_spend():
    policy = _policy_with_fake_pricing()
    cost = Cost()
    inner = FakeModel({"s": "ok"})
    metered = MeteredModel(inner, policy, cost, per_run_cap_usd=None)
    metered.complete(system="sys", prompt="hello world", tier=ModelTier.DRAFTER, step="s")
    assert len(cost.entries) == 1
    assert cost.entries[0].kind == "model"
    assert cost.total_usd > 0


def test_per_run_cap_halts_runaway():
    policy = _policy_with_fake_pricing()
    cost = Cost()
    # long response -> more output tokens -> more cost per call
    inner = FakeModel({"s": "x" * 4000})
    metered = MeteredModel(inner, policy, cost, per_run_cap_usd=0.0001)
    with pytest.raises(PerRunBudgetExceeded):
        for _ in range(100):
            metered.complete(system="s", prompt="p", tier=ModelTier.DRAFTER, step="s")
    # the spend up to the halt is still recorded
    assert cost.total_usd > 0.0001


def test_governor_day_cap_and_rollup():
    policy = _policy_with_fake_pricing()
    gov = BudgetGovernor(policy, per_run_cap_usd=1.0, per_day_cap_usd=0.00003)
    inner = FakeModel({"s": "hello"})

    budget = gov.begin(inner)
    budget.model.complete(system="s", prompt="p", tier=ModelTier.DRAFTER, step="s")
    gov.end(budget)
    assert gov.day_spent_usd > 0

    # next begin should halt because the day cap is now reached
    assert gov.day_cap_reached()
    with pytest.raises(DayBudgetHalt):
        gov.begin(inner)


def test_governor_kill_switch():
    gov = BudgetGovernor(ModelPolicy(), per_run_cap_usd=1.0, per_day_cap_usd=10.0,
                         kill_switch=True)
    with pytest.raises(KillSwitchEngaged):
        gov.begin(FakeModel())
