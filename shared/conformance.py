"""The conformance suite every qualifier passes before it is registered.

Because the shell trusts the interior, this is the gate that keeps qualifiers
faithful as they are added and changed. On a fixed test set a qualifier must
return:
  - a well-formed dossier with provenance on every Claim,
  - a Disposition that references real Claims by id,
  - a draft (when the disposition is call) that passes the boundary grounding
    gate, and none when it is not,
  - all within a per-run cost bound.

A qualifier that returns a thin or sloppy dossier, an ungrounded draft, or blows
the cost bound fails here and is not registered.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from engine.cost import BudgetGovernor, ModelPolicy, PerRunBudgetExceeded
from engine.factcheck import factcheck
from shared.contracts import DispositionKind, RunResult
from shared.model import ModelClient
from shared.tools.fetchers import (
    StubCompanyFetcher,
    StubCrmFetcher,
    StubPersonFetcher,
    StubUsageFetcher,
    World,
)
from shared.tools.toolbox import build_toolbox


@dataclass
class ConformanceCase:
    """One fixed test: a world, the task to run, and the scripted model that
    drives the qualifier deterministically."""

    name: str
    world: World
    task_id: str
    model: ModelClient


@dataclass
class CaseResult:
    name: str
    violations: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.violations


@dataclass
class ConformanceReport:
    qualifier: str
    cases: list[CaseResult]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.cases)

    def summary(self) -> str:
        lines = [f"conformance: {self.qualifier} -> {'PASS' if self.passed else 'FAIL'}"]
        for c in self.cases:
            tag = "ok" if c.passed else "FAIL"
            lines.append(f"  [{tag}] {c.name} (${c.cost_usd})")
            for v in c.violations:
                lines.append(f"      - {v}")
        return "\n".join(lines)


def check_result(result: RunResult, factcheck_model: ModelClient) -> list[str]:
    """Pure structural + grounding checks on a qualifier's output."""
    violations: list[str] = []

    if not result.dossier:
        violations.append("empty dossier")
    dossier_ids = {c.id for c in result.dossier}
    for c in result.dossier:
        if not c.id:
            violations.append("claim with no id")
        if not c.source:
            violations.append(f"claim {c.id} has no source")
        if c.raw in (None, {}, [], ""):
            violations.append(f"claim {c.id} has no provenance (raw)")
        if not (0.0 <= float(c.confidence) <= 1.0):
            violations.append(f"claim {c.id} confidence out of range: {c.confidence}")

    disp = result.disposition
    if disp is None:
        violations.append("no disposition")
        return violations
    if not disp.reasoning.strip():
        violations.append("disposition has empty reasoning")
    if not (0.0 <= float(disp.confidence) <= 1.0):
        violations.append(f"disposition confidence out of range: {disp.confidence}")
    for ref in disp.claim_refs:
        if ref not in dossier_ids:
            violations.append(f"disposition references unknown claim {ref}")

    if disp.disposition == DispositionKind.CALL:
        if not disp.claim_refs:
            violations.append("call disposition references no Claims")
        if result.draft is None:
            violations.append("call disposition produced no draft")
        else:
            if not result.draft.to:
                violations.append("draft has no recipient")
            fc = factcheck(result.draft, result.dossier, factcheck_model)
            if not fc.passed:
                bad = "; ".join(a.text for a in fc.ungrounded)
                violations.append(f"draft failed grounding gate: {bad}")
    else:
        if result.draft is not None:
            violations.append(f"{disp.disposition.value} disposition produced a draft")

    return violations


def run_conformance(
    qualifier: Any,
    cases: list[ConformanceCase],
    *,
    policy: ModelPolicy | None = None,
    per_run_usd_cap: float = 1.0,
    voice_profile: str = "Plain prose.",
    exemplars: list[str] | None = None,
    signature: str = "",
) -> ConformanceReport:
    """Run a qualifier against the fixed test set and report violations."""
    policy = policy or ModelPolicy()
    results: list[CaseResult] = []

    for case in cases:
        gov = BudgetGovernor(
            policy, per_run_cap_usd=per_run_usd_cap, per_day_cap_usd=math.inf
        )
        budget = gov.begin(case.model)
        toolbox = build_toolbox(
            crm_fetcher=StubCrmFetcher(case.world),
            person_fetcher=StubPersonFetcher(case.world),
            company_fetcher=StubCompanyFetcher(case.world),
            usage_fetcher=StubUsageFetcher(case.world),
            model=budget.model,
            voice_profile=voice_profile,
            exemplars=exemplars or [],
            signature=signature,
        )
        record = case.world.tasks[case.task_id]
        try:
            result = qualifier.run(case.task_id, record, toolbox)
            violations = check_result(result, budget.model)
        except PerRunBudgetExceeded as exc:
            violations = [f"exceeded per-run cost cap: {exc}"]
        finally:
            gov.end(budget)
        results.append(
            CaseResult(name=case.name, violations=violations, cost_usd=budget.cost.total_usd)
        )

    return ConformanceReport(qualifier=getattr(qualifier, "name", "?"), cases=results)
