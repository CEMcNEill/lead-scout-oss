"""The ledger: SQLite, stdlib only.

The most important artifact in the system and the medium the three loops share.
It does four jobs: dedup (the fast loop only processes task_ids not already
here), audit trail, learning corpus, and efficacy analytics.

Storage model: each LeadRun is persisted as one canonical JSON blob, with a
handful of columns promoted out of it for dedup and analytics (task_id, status,
lead_type, cost, ...). The blob is the source of truth; promoted columns are
derived on every write so they can never drift.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from shared.contracts import LeadRun

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lead_runs (
    id                   TEXT PRIMARY KEY,
    task_id              TEXT NOT NULL UNIQUE,
    rep_id               TEXT NOT NULL,
    ts                   TEXT NOT NULL,
    status               TEXT NOT NULL,
    lead_type            TEXT,
    qualifier            TEXT,
    disposition          TEXT,
    confidence           REAL,
    total_usd            REAL NOT NULL DEFAULT 0,
    voice_profile_version TEXT,
    rubric_version       TEXT,
    model_policy_version TEXT,
    next_touch_due       TEXT,
    replied              INTEGER,
    blob                 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lead_runs_task ON lead_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_lead_runs_rep  ON lead_runs(rep_id);
CREATE INDEX IF NOT EXISTS idx_lead_runs_ts   ON lead_runs(ts);
"""

# Columns promoted from the blob after the original schema shipped. Added to an
# existing DB with a guarded ALTER on open so old ledgers keep working.
_MIGRATIONS = [
    ("next_touch_due", "ALTER TABLE lead_runs ADD COLUMN next_touch_due TEXT"),
    ("replied", "ALTER TABLE lead_runs ADD COLUMN replied INTEGER"),
]


class Ledger:
    """A thin, inspectable wrapper over a SQLite file.

    Open one per process. Safe to pass a file path or ":memory:" (tests that
    need persistence across connections should use a temp file).
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path not in (":memory:", ""):
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns promoted after the original schema, idempotently. CREATE
        TABLE handles fresh DBs; this catches DBs created before a column existed."""
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(lead_runs)")
        }
        for column, ddl in _MIGRATIONS:
            if column not in existing:
                self._conn.execute(ddl)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lead_runs_due ON lead_runs(next_touch_due)"
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- dedup -----------------------------------------------------------

    def has_task(self, task_id: str) -> bool:
        """The dedup check the fast loop runs before processing a task."""
        cur = self._conn.execute(
            "SELECT 1 FROM lead_runs WHERE task_id = ? LIMIT 1", (task_id,)
        )
        return cur.fetchone() is not None

    def seen_task_ids(self) -> set[str]:
        cur = self._conn.execute("SELECT task_id FROM lead_runs")
        return {row["task_id"] for row in cur.fetchall()}

    # --- write -----------------------------------------------------------

    def insert(self, run: LeadRun) -> None:
        """Insert a new run. Raises sqlite3.IntegrityError if the run id or
        task_id already exists, which protects the dedup invariant at the
        storage layer."""
        self._conn.execute(
            """
            INSERT INTO lead_runs (
                id, task_id, rep_id, ts, status, lead_type, qualifier,
                disposition, confidence, total_usd,
                voice_profile_version, rubric_version, model_policy_version,
                next_touch_due, replied, blob
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            self._row_params(run),
        )
        self._conn.commit()

    def update(self, run: LeadRun) -> None:
        """Replace an existing run by id. Used by the human and slow loops to
        fill in human_disposition, sent_draft, outcome, etc."""
        params = self._row_params(run)
        # move id to the end for the WHERE clause, drop it from the SET list head
        (
            id_,
            task_id,
            rep_id,
            ts,
            status,
            lead_type,
            qualifier,
            disposition,
            confidence,
            total_usd,
            vpv,
            rv,
            mpv,
            next_touch_due,
            replied,
            blob,
        ) = params
        cur = self._conn.execute(
            """
            UPDATE lead_runs SET
                task_id=?, rep_id=?, ts=?, status=?, lead_type=?, qualifier=?,
                disposition=?, confidence=?, total_usd=?,
                voice_profile_version=?, rubric_version=?, model_policy_version=?,
                next_touch_due=?, replied=?, blob=?
            WHERE id=?
            """,
            (
                task_id, rep_id, ts, status, lead_type, qualifier,
                disposition, confidence, total_usd, vpv, rv, mpv,
                next_touch_due, replied, blob, id_,
            ),
        )
        if cur.rowcount == 0:
            raise KeyError(f"no run with id {id_!r} to update")
        self._conn.commit()

    def upsert(self, run: LeadRun) -> None:
        if self.get(run.id) is None:
            self.insert(run)
        else:
            self.update(run)

    def delete(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM lead_runs WHERE id = ?", (run_id,))
        self._conn.commit()

    def replace_by_task(self, run: LeadRun) -> None:
        """Persist `run` as the single row for its task_id, dropping any prior run
        for that task. The fast loop dedups so it never re-runs a task; this makes
        manual re-runs idempotent (last run wins) without violating the task_id
        uniqueness that dedup relies on."""
        existing = self.get_by_task(run.task_id)
        if existing is not None and existing.id != run.id:
            self.delete(existing.id)
        self.upsert(run)

    # --- read ------------------------------------------------------------

    def get(self, run_id: str) -> LeadRun | None:
        cur = self._conn.execute("SELECT blob FROM lead_runs WHERE id = ?", (run_id,))
        row = cur.fetchone()
        return LeadRun.from_dict(json.loads(row["blob"])) if row else None

    def get_by_task(self, task_id: str) -> LeadRun | None:
        cur = self._conn.execute(
            "SELECT blob FROM lead_runs WHERE task_id = ?", (task_id,)
        )
        row = cur.fetchone()
        return LeadRun.from_dict(json.loads(row["blob"])) if row else None

    def list_runs(
        self,
        *,
        rep_id: str | None = None,
        status: str | None = None,
        lead_type: str | None = None,
        limit: int | None = None,
    ) -> list[LeadRun]:
        """Query for the learning loops and analytics. Filters compose."""
        clauses: list[str] = []
        args: list[Any] = []
        if rep_id is not None:
            clauses.append("rep_id = ?")
            args.append(rep_id)
        if status is not None:
            clauses.append("status = ?")
            args.append(status)
        if lead_type is not None:
            clauses.append("lead_type = ?")
            args.append(lead_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT blob FROM lead_runs{where} ORDER BY ts ASC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        cur = self._conn.execute(sql, args)
        return [LeadRun.from_dict(json.loads(r["blob"])) for r in cur.fetchall()]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM lead_runs").fetchone()["n"]

    # --- follow-up ------------------------------------------------------

    def runs_due_for_followup(self, now_iso: str, *, rep_id: str | None = None) -> list[LeadRun]:
        """Runs whose next follow-up is due at or before now and that have not been
        replied to. The follow-up poll reads this; the fast loop never does. ts is
        UTC ISO so a string compare is a time compare. A run with replied=1 or a
        null due time is excluded (sequence complete, replied, or single-touch)."""
        clauses = ["next_touch_due IS NOT NULL", "next_touch_due <= ?",
                   "(replied IS NULL OR replied = 0)"]
        args: list[Any] = [now_iso]
        if rep_id is not None:
            clauses.append("rep_id = ?")
            args.append(rep_id)
        sql = (
            "SELECT blob FROM lead_runs WHERE " + " AND ".join(clauses)
            + " ORDER BY next_touch_due ASC"
        )
        cur = self._conn.execute(sql, args)
        return [LeadRun.from_dict(json.loads(r["blob"])) for r in cur.fetchall()]

    # --- internal --------------------------------------------------------

    @staticmethod
    def _row_params(run: LeadRun) -> tuple[Any, ...]:
        disposition = run.disposition.disposition.value if run.disposition else None
        confidence = run.disposition.confidence if run.disposition else None
        replied = None if run.outcome.replied is None else int(bool(run.outcome.replied))
        return (
            run.id,
            run.task_id,
            run.rep_id,
            run.ts,
            run.status.value,
            run.route.lead_type,
            run.route.qualifier,
            disposition,
            confidence,
            run.cost.total_usd,
            run.voice_profile_version,
            run.rubric_version,
            run.model_policy_version,
            run.next_touch_due,
            replied,
            json.dumps(run.to_dict()),
        )
