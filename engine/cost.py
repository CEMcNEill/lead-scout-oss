"""Cost control: model policy, cost meter, budget governor.

A circuit breaker for anomalies, not a throttle on quality. In normal operation
it never fires and never downgrades a judgment; it exists to catch the
pathological case (a runaway agentic loop, a lead flood, an enrichment retry
storm). It lives in the infrastructure layer and never touches the judgment
layer: the meter wraps the model client as middleware, the governor wraps the
run.

- Model policy maps each call's tier to a concrete model by stakes, and prices
  the spend.
- The cost meter records every Claude call into the run's Cost and enforces the
  per-run cap at the point of spend, so a runaway interior is stopped mid-loop.
- The budget governor enforces the per-day cap and the global kill switch around
  whole runs: when the day cap is hit it stops starting new runs, in-flight runs
  finish, nothing new starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.contracts import Cost, CostEntry
from shared.model import ModelClient, ModelResponse, ModelTier


# --- model policy --------------------------------------------------------


@dataclass(frozen=True)
class Pricing:
    """USD per million tokens, in/out. Maintained here; updating a price is a
    config edit, not a code change elsewhere."""

    input_per_mtok: float
    output_per_mtok: float


# Default tier -> model. Stakes-based, per the spec's model policy.
_DEFAULT_TIERS: dict[str, str] = {
    ModelTier.ROUTING_FALLBACK: "claude-haiku-4-5-20251001",
    ModelTier.RESEARCH_SYNTHESIS: "claude-sonnet-4-6",
    ModelTier.QUALIFIER_JUDGMENT: "claude-opus-4-8",
    ModelTier.DRAFTER: "claude-opus-4-8",
    ModelTier.LEARNING: "claude-opus-4-8",
}

# Known public price points (USD / Mtok). Editable; the source of truth for cost.
_DEFAULT_PRICING: dict[str, Pricing] = {
    "claude-opus-4-8": Pricing(15.0, 75.0),
    "claude-sonnet-4-6": Pricing(3.0, 15.0),
    "claude-haiku-4-5-20251001": Pricing(0.80, 4.0),
}


@dataclass(frozen=True)
class ModelPolicy:
    version: str = "2026-06-mvp"
    tiers: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_TIERS))
    pricing: dict[str, Pricing] = field(default_factory=lambda: dict(_DEFAULT_PRICING))

    def model_for(self, tier: str) -> str:
        if tier not in self.tiers:
            raise KeyError(f"no model mapped for tier {tier!r}")
        return self.tiers[tier]

    def cost_usd(self, model: str, tokens_in: int, tokens_out: int) -> float:
        p = self.pricing.get(model)
        if p is None:
            return 0.0  # unknown model (e.g. a test fake) meters at zero
        return round(
            tokens_in / 1_000_000 * p.input_per_mtok
            + tokens_out / 1_000_000 * p.output_per_mtok,
            6,
        )


# --- exceptions ----------------------------------------------------------


class PerRunBudgetExceeded(Exception):
    """A single run blew past its ceiling: almost certainly a runaway interior."""


class DayBudgetHalt(Exception):
    """The per-day cap is hit; no new runs start until the rep lifts it."""


class KillSwitchEngaged(Exception):
    """The global kill switch is on."""


# --- cost meter ----------------------------------------------------------


class MeteredModel:
    """Wraps a ModelClient, recording every call's spend into a run's Cost and
    enforcing the per-run cap. Because it sees every completion, it can halt a
    runaway agentic loop mid-flight."""

    def __init__(
        self,
        inner: ModelClient,
        policy: ModelPolicy,
        cost: Cost,
        per_run_cap_usd: float | None,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._cost = cost
        self._cap = per_run_cap_usd

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        tier: str,
        step: str,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        resp = self._inner.complete(
            system=system, prompt=prompt, tier=tier, step=step, max_tokens=max_tokens
        )
        usd = self._policy.cost_usd(resp.model, resp.tokens_in, resp.tokens_out)
        self._cost.add(
            CostEntry(
                step=step,
                kind="model",
                detail=resp.model,
                tokens_in=resp.tokens_in,
                tokens_out=resp.tokens_out,
                usd=usd,
            )
        )
        if self._cap is not None and self._cost.total_usd > self._cap:
            raise PerRunBudgetExceeded(
                f"run cost {self._cost.total_usd} exceeded per-run cap {self._cap}"
            )
        return resp


def record_tool_cost(cost: Cost, *, step: str, provider: str, usd: float) -> None:
    """Hook for paid tool calls (Clay credits, etc.) to report into the same
    ledger. Phase 1 stubs are free; real providers call this."""
    cost.add(CostEntry(step=step, kind="tool", detail=provider, usd=usd))


# --- budget governor -----------------------------------------------------


@dataclass
class RunBudget:
    """Handed to the shell for one run: the metered model to use and the Cost it
    accrues into."""

    model: MeteredModel
    cost: Cost


class BudgetGovernor:
    """Wraps whole runs. Enforces the per-day cap and kill switch around runs;
    delegates the per-run cap to the MeteredModel it hands out."""

    def __init__(
        self,
        policy: ModelPolicy,
        *,
        per_run_cap_usd: float,
        per_day_cap_usd: float,
        kill_switch: bool = False,
    ) -> None:
        self.policy = policy
        self.per_run_cap_usd = per_run_cap_usd
        self.per_day_cap_usd = per_day_cap_usd
        self.kill_switch = kill_switch
        self.day_spent_usd = 0.0

    def begin(self, inner_model: ModelClient) -> RunBudget:
        """Open a run's budget. Raises if the system should not start new work."""
        if self.kill_switch:
            raise KillSwitchEngaged("global kill switch engaged")
        if self.day_spent_usd >= self.per_day_cap_usd:
            raise DayBudgetHalt(
                f"day spend {self.day_spent_usd} reached cap {self.per_day_cap_usd}"
            )
        cost = Cost()
        metered = MeteredModel(inner_model, self.policy, cost, self.per_run_cap_usd)
        return RunBudget(model=metered, cost=cost)

    def end(self, budget: RunBudget) -> None:
        """Close a run, rolling its spend into the day total. Always called, even
        when the run failed, so partial spend still counts."""
        self.day_spent_usd = round(self.day_spent_usd + budget.cost.total_usd, 6)

    def day_cap_reached(self) -> bool:
        return self.day_spent_usd >= self.per_day_cap_usd
