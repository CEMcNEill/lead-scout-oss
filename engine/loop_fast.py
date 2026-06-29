"""The fast loop: poll, dedup, dispatch, stage.

Runs on the machine clock (every 5 minutes via launchd). It only writes the
ledger; it never invokes another loop. For each open task not already in the
ledger it calls the shell, which does the interior work and stages a draft.

Dedup lives here (step 0): the loop skips task_ids already present, which is why
re-polling the same tasks is cheap and safe.

When the budget governor halts new runs (day cap or kill switch), the sweep stops
starting work and reports it. In-flight runs are not interrupted; nothing new
starts until the rep lifts the cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from engine.cost import DayBudgetHalt, KillSwitchEngaged
from engine.shell import Shell
from shared.contracts import LeadRun, RepConfig, TriggerMeta, TriggerSource


class TaskSource(Protocol):
    def poll(self, rep_config: RepConfig) -> list[str]:
        """Return the ids of open lead tasks for this rep."""
        ...


@dataclass
class SweepResult:
    processed: list[LeadRun] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already in the ledger
    halted: bool = False
    halt_reason: str | None = None

    @property
    def staged(self) -> int:
        from shared.contracts import RunStatus

        return sum(1 for r in self.processed if r.status == RunStatus.STAGED_FOR_REVIEW)


class FastLoop:
    def __init__(self, shell: Shell, task_source: TaskSource) -> None:
        self._shell = shell
        self._source = task_source

    def sweep(
        self, rep_config: RepConfig, *, trigger_source: TriggerSource = TriggerSource.BATCH
    ) -> SweepResult:
        result = SweepResult()
        trigger_meta = TriggerMeta(source=trigger_source, timestamp="")
        for task_id in self._source.poll(rep_config):
            if self._shell.ledger.has_task(task_id):  # step 0: dedup
                result.skipped.append(task_id)
                continue
            try:
                run = self._shell.process_lead_run(task_id, rep_config, trigger_meta)
            except (DayBudgetHalt, KillSwitchEngaged) as exc:
                # stop starting new runs; the rep is told and decides whether to lift
                result.halted = True
                result.halt_reason = str(exc)
                break
            result.processed.append(run)
        return result
