"""Deterministic ledger tests: dedup, round-trip persistence, update, query.

Dedup is a system invariant (the fast loop relies on it), so it gets explicit
coverage including the storage-layer guard against a duplicate task_id.
"""

import sqlite3

import pytest

from engine.ledger import Ledger
from shared.contracts import (
    Claim,
    Cost,
    CostEntry,
    Disposition,
    DispositionKind,
    Draft,
    LeadRun,
    Route,
    RunStatus,
    Target,
    TriggerSource,
)


def _run(run_id: str, task_id: str, *, status=RunStatus.STAGED_FOR_REVIEW) -> LeadRun:
    return LeadRun(
        id=run_id,
        task_id=task_id,
        rep_id="rep_chris",
        trigger_source=TriggerSource.BATCH,
        ts="2026-06-28T12:00:00Z",
        route=Route(lead_type="big_fish", qualifier="big_fish"),
        status=status,
        dossier=[
            Claim(id="c1", field="seniority", value="VP", source="person_research",
                  raw={"title": "VP"}, confidence=0.9),
        ],
        disposition=Disposition(
            disposition=DispositionKind.CALL,
            reasoning="clears bar (c1)",
            confidence=0.8,
            claim_refs=["c1"],
            target=Target(name="Dana", email="dana@acme.com"),
        ),
        staged_draft=Draft(to="dana@acme.com", subject="hi", body="...", angle="usage-led"),
        cost=Cost(entries=[CostEntry(step="judgment", kind="model", detail="opus", usd=0.04)]),
    )


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


def test_insert_and_get(ledger):
    run = _run("run_1", "task_1")
    ledger.insert(run)
    assert ledger.get("run_1") == run
    assert ledger.get_by_task("task_1") == run
    assert ledger.count() == 1


def test_dedup_has_task(ledger):
    assert not ledger.has_task("task_1")
    ledger.insert(_run("run_1", "task_1"))
    assert ledger.has_task("task_1")
    assert ledger.seen_task_ids() == {"task_1"}


def test_duplicate_task_id_rejected(ledger):
    ledger.insert(_run("run_1", "task_1"))
    with pytest.raises(sqlite3.IntegrityError):
        ledger.insert(_run("run_2", "task_1"))


def test_duplicate_run_id_rejected(ledger):
    ledger.insert(_run("run_1", "task_1"))
    with pytest.raises(sqlite3.IntegrityError):
        ledger.insert(_run("run_1", "task_2"))


def test_update_fills_in_fields(ledger):
    run = _run("run_1", "task_1")
    ledger.insert(run)
    run.human_disposition = "self_serve"
    run.human_rationale = "they can self-onboard"
    run.outcome.replied = True
    ledger.update(run)
    back = ledger.get("run_1")
    assert back.human_disposition == "self_serve"
    assert back.outcome.replied is True


def test_update_missing_raises(ledger):
    with pytest.raises(KeyError):
        ledger.update(_run("ghost", "task_x"))


def test_upsert(ledger):
    run = _run("run_1", "task_1")
    ledger.upsert(run)  # insert path
    run.status = RunStatus.BLOCKED
    ledger.upsert(run)  # update path
    assert ledger.get("run_1").status == RunStatus.BLOCKED
    assert ledger.count() == 1


def test_list_runs_filters(ledger):
    ledger.insert(_run("run_1", "task_1", status=RunStatus.STAGED_FOR_REVIEW))
    ledger.insert(_run("run_2", "task_2", status=RunStatus.BLOCKED))
    ledger.insert(_run("run_3", "task_3", status=RunStatus.STAGED_FOR_REVIEW))
    staged = ledger.list_runs(status="staged_for_review")
    assert {r.id for r in staged} == {"run_1", "run_3"}
    assert len(ledger.list_runs(rep_id="rep_chris")) == 3
    assert ledger.list_runs(limit=1) and len(ledger.list_runs(limit=1)) == 1


def test_persistence_across_connections(tmp_path):
    path = tmp_path / "ledger.db"
    led = Ledger(path)
    led.insert(_run("run_1", "task_1"))
    led.close()
    reopened = Ledger(path)
    assert reopened.has_task("task_1")
    assert reopened.get("run_1").disposition.disposition == DispositionKind.CALL
    reopened.close()


def test_promoted_columns_match_blob(ledger):
    ledger.insert(_run("run_1", "task_1"))
    row = ledger._conn.execute(
        "SELECT lead_type, qualifier, disposition, total_usd FROM lead_runs WHERE id='run_1'"
    ).fetchone()
    assert row["lead_type"] == "big_fish"
    assert row["disposition"] == "call"
    assert row["total_usd"] == 0.04
