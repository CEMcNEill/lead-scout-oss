"""Trigger adapters around the shell.

BatchAdapter is the 5-minute localhost poll (the fast loop, and likely all
that's needed). ManualAdapter runs one lead on demand. WebhookAdapter is Phase 2
and intentionally absent.

A TaskSource feeds the batch loop the open task ids for a rep. Phase 1 ships a
stub source over fixtures; the real source is a Salesforce SOQL query for open
lead tasks assigned to the rep (wired with SF auth).
"""

from __future__ import annotations

from engine.loop_fast import FastLoop, SweepResult, TaskSource
from engine.shell import Shell
from shared.contracts import LeadRun, RepConfig, TriggerMeta, TriggerSource
from shared.tools.fetchers import World


class StubTaskSource:
    """Returns a fixed list of task ids (Phase 1). Either an explicit list or all
    task ids in a fixtures World."""

    def __init__(self, task_ids: list[str]) -> None:
        self._task_ids = list(task_ids)

    @classmethod
    def from_world(cls, world: World) -> "StubTaskSource":
        return cls(list(world.tasks.keys()))

    def poll(self, rep_config: RepConfig) -> list[str]:
        return list(self._task_ids)


class ManualAdapter:
    """Run one lead on demand. Bypasses dedup on purpose; the shell replaces any
    prior run for the task so a re-run is idempotent."""

    def __init__(self, shell: Shell) -> None:
        self._shell = shell

    def run(self, task_id: str, rep_config: RepConfig) -> LeadRun:
        return self._shell.process_lead_run(
            task_id, rep_config, TriggerMeta(source=TriggerSource.MANUAL, timestamp="")
        )


class BatchAdapter:
    """The 5-minute poll. Wraps the fast loop; one call is one sweep."""

    def __init__(self, shell: Shell, task_source: TaskSource) -> None:
        self._loop = FastLoop(shell, task_source)

    def sweep(self, rep_config: RepConfig) -> SweepResult:
        return self._loop.sweep(rep_config, trigger_source=TriggerSource.BATCH)
