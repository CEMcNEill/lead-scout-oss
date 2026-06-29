"""The shell: the thin deterministic boundary around the agentic interior.

Implements process_lead_run, the keystone unit of work and the body of the fast
loop. The shell owns the edges (read, route, hard-stops, the grounding gate,
staging, the ledger, cost) and trusts the qualifier with the interior.

    0  dedup is the loop's job (it skips task_ids already in the ledger)
    1  read the CRM record (ground truth)
    2  route to a qualifier via the registry
    3  hard-stops check
    4  qualifier.run -> dossier, disposition, draft        (the interior)
    5  boundary fact-check gate over the draft
    6  stage the clean draft; post the review card          (Phase 1.5 surfaces)
    7  write the ledger (with cost and versions)

Cost is handled as middleware: the budget governor wraps the run and hands the
qualifier a metered model, so a runaway interior is stopped at the point of spend
and the day cap halts new runs without touching the judgment layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.cost import BudgetGovernor, PerRunBudgetExceeded
from engine.factcheck import factcheck
from engine.hardstops import HardStopConfig, check_hard_stops
from engine.ledger import Ledger
from engine.providers import (
    Clock,
    Notifier,
    NullNotifier,
    RealClock,
    StagingSink,
    ToolProvider,
)
from engine.router import Router
from shared.contracts import LeadRun, RepConfig, RunStatus, TriggerMeta
from shared.model import ModelClient
from shared.registry import QualifierRegistry
from shared.tools.toolbox import build_toolbox


@dataclass
class Shell:
    ledger: Ledger
    router: Router
    registry: QualifierRegistry
    hard_stops: HardStopConfig
    governor: BudgetGovernor
    inner_model: ModelClient
    tool_provider: ToolProvider
    staging_sink: StagingSink
    notifier: Notifier = NullNotifier()
    clock: Clock = RealClock()
    rubric_version: str = "v1"

    def process_lead_run(
        self, task_id: str, rep_config: RepConfig, trigger_meta: TriggerMeta
    ) -> LeadRun:
        run_id = self.clock.new_run_id()
        ts = trigger_meta.timestamp or self.clock.now()

        crm_fetcher, person_fetcher, company_fetcher, usage_fetcher = (
            self.tool_provider.fetchers(rep_config)
        )

        # 1 + 2: read for routing/hard-stops, then route
        record = crm_fetcher.read(task_id)
        route = self.router.route(record)

        # 3: hard-stops, before any qualifier or model spend
        stops = check_hard_stops(record, self.hard_stops)
        if stops:
            run = LeadRun(
                id=run_id, task_id=task_id, rep_id=rep_config.rep_id,
                trigger_source=trigger_meta.source, ts=ts, route=route,
                status=RunStatus.BLOCKED, hard_stops=stops,
                rubric_version=self.rubric_version,
                model_policy_version=self.governor.policy.version,
            )
            self._persist(run)
            return run

        # cost middleware: open the run budget (may raise DayBudgetHalt /
        # KillSwitchEngaged, which the loop handles by stopping new runs)
        budget = self.governor.begin(self.inner_model)
        voice_text, voice_version = self.tool_provider.voice(rep_config)
        exemplars = self.tool_provider.exemplars(rep_config, route.lead_type)

        run = LeadRun(
            id=run_id, task_id=task_id, rep_id=rep_config.rep_id,
            trigger_source=trigger_meta.source, ts=ts, route=route,
            status=RunStatus.ERROR, cost=budget.cost,
            voice_profile_version=voice_version, rubric_version=self.rubric_version,
            model_policy_version=self.governor.policy.version,
        )

        try:
            toolbox = build_toolbox(
                crm_fetcher=crm_fetcher, person_fetcher=person_fetcher,
                company_fetcher=company_fetcher, usage_fetcher=usage_fetcher,
                model=budget.model, voice_profile=voice_text, exemplars=exemplars,
                signature=rep_config.signature, calendar_url=rep_config.calendar_url,
            )
            qualifier = self.registry.dispatch(route)

            # 4: the agentic interior
            result = qualifier.run(task_id, record, toolbox)
            run.dossier = result.dossier
            run.disposition = result.disposition

            # 5: the boundary fact-check gate over the returned draft
            draft = result.draft
            if draft is not None:
                fc = factcheck(draft, result.dossier, budget.model)
                if not fc.passed:
                    # strip the unsafe draft; record what was flagged. Never stage
                    # an ungrounded draft. The disposition still goes to review.
                    run.factcheck_flags = [a.text for a in fc.ungrounded]
                    draft = None

            # 6: stage a clean draft; post the review card (notifier is a Phase 1.5
            # surface, no-op now)
            if draft is not None:
                run.staged_draft_ref = self.staging_sink.stage(run_id, rep_config, draft)
                run.staged_draft = draft

            run.status = RunStatus.STAGED_FOR_REVIEW

        except PerRunBudgetExceeded as exc:
            # a runaway interior: hard-stop and flag, per the spec
            run.status = RunStatus.BLOCKED
            run.hard_stops = ["per_run_budget_exceeded"]
            run.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - the shell must never crash the loop
            run.status = RunStatus.ERROR
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.governor.end(budget)

        run.slack_thread_ref = self.notifier.notify(run, rep_config)
        self._persist(run)
        return run

    def _persist(self, run: LeadRun) -> None:
        # cost is already rolled into run.cost via the metered model. replace_by_task
        # keeps one row per task (dedup invariant) while allowing manual re-runs.
        self.ledger.replace_by_task(run)
