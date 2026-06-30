"""Phase 0: native tool-use in the model layer. FakeModel turn scripting,
MeteredModel metering + cap on run_turn, the Opus pricing fix, and the new
orchestration tier. No live calls."""

import pytest

from engine.cost import MeteredModel, ModelPolicy, PerRunBudgetExceeded
from shared.contracts import Cost
from shared.model import FakeModel, ModelTier, ToolSpec


def _tools():
    return [ToolSpec(name="crm_context", description="read crm",
                     input_schema={"type": "object", "properties": {}})]


def test_fake_run_turn_yields_ordered_turns_via_cursor():
    fm = FakeModel(tool_script={"x.agent": [
        {"tool_calls": [{"name": "crm_context", "input": {}}]},
        {"text": "done", "stop": "end_turn"},
    ]})
    t1 = fm.run_turn(system="s", messages=[{"role": "user", "content": "go"}],
                     tools=_tools(), tier=ModelTier.AGENT_ORCHESTRATION, step="x.agent")
    assert t1.stop_reason == "tool_use"
    assert [c.name for c in t1.tool_calls] == ["crm_context"]
    t2 = fm.run_turn(system="s", messages=[], tools=_tools(),
                     tier=ModelTier.AGENT_ORCHESTRATION, step="x.agent")
    assert t2.stop_reason == "end_turn" and t2.tool_calls == [] and t2.text == "done"
    with pytest.raises(KeyError):  # script exhausted
        fm.run_turn(system="s", messages=[], tools=_tools(),
                    tier=ModelTier.AGENT_ORCHESTRATION, step="x.agent")


def test_fake_run_turn_unscripted_step_raises():
    with pytest.raises(KeyError):
        FakeModel().run_turn(system="s", messages=[], tools=_tools(),
                             tier="t", step="missing.agent")


def test_metered_run_turn_records_one_entry():
    policy = ModelPolicy()
    cost = Cost()
    inner = FakeModel(model="claude-sonnet-4-6",
                      tool_script={"a.agent": [{"tool_calls": [{"name": "crm_context"}]}]})
    metered = MeteredModel(inner, policy, cost, per_run_cap_usd=None)
    metered.run_turn(system="s", messages=[{"role": "user", "content": "x" * 400}],
                     tools=_tools(), tier=ModelTier.AGENT_ORCHESTRATION, step="a.agent")
    assert len(cost.entries) == 1 and cost.entries[0].kind == "model"
    assert cost.total_usd > 0  # priced model (default "fake-model" would meter $0)


def test_metered_run_turn_trips_the_cap_with_a_priced_model():
    policy = ModelPolicy()
    cost = Cost()
    inner = FakeModel(model="claude-opus-4-8",
                      tool_script={"a.agent": [{"tool_calls": [{"name": "crm_context"}]}
                                               for _ in range(100)]})
    metered = MeteredModel(inner, policy, cost, per_run_cap_usd=0.0000001)
    with pytest.raises(PerRunBudgetExceeded):
        for _ in range(100):
            metered.run_turn(system="s", messages=[{"role": "user", "content": "x" * 4000}],
                             tools=_tools(), tier=ModelTier.AGENT_ORCHESTRATION, step="a.agent")
    assert cost.total_usd > 0.0000001  # spend up to the halt is recorded


def test_opus_pricing_fixed_and_orchestration_tier():
    policy = ModelPolicy()
    assert policy.model_for(ModelTier.AGENT_ORCHESTRATION) == "claude-sonnet-4-6"
    # Opus 4.8 is $5/$25 per Mtok -> 5 + 25 = 30.0 for 1M in / 1M out
    assert policy.cost_usd("claude-opus-4-8", 1_000_000, 1_000_000) == 30.0
    assert policy.cost_usd("claude-opus-4-8", 1_000_000, 1_000_000) != 90.0  # not the old 15/75
