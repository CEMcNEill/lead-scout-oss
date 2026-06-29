"""Fetcher layer: deterministic data access, no assessment.

Each external system (Salesforce, Clay, PostHog) sits behind a narrow Protocol
that returns raw data and nothing more. Phase 1 ships stub implementations
backed by JSON fixtures so the whole engine runs end-to-end on fake data; the
real implementations slot in behind the same Protocols without touching the
synthesis layer or the qualifiers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class CrmFetcher(Protocol):
    def read(self, task_id: str) -> dict[str, Any]:
        """Ground truth from Salesforce. For inbound, carries the message text."""
        ...


class PersonFetcher(Protocol):
    def enrich(self, person_ref: dict[str, Any]) -> dict[str, Any]:
        """Clay/LinkedIn enrichment for one person."""
        ...


class CompanyFetcher(Protocol):
    def enrich(self, domain: str) -> dict[str, Any]:
        """Firmographic + technographic enrichment for one company."""
        ...


class UsageFetcher(Protocol):
    def query(self, ref: Any) -> dict[str, Any]:
        """Account usage. `ref` is an account id, or the CRM record when account
        resolution needs more than an id (company name, domain, org hints)."""
        ...


# --- fixture-backed stubs -----------------------------------------------


@dataclass
class World:
    """A bundle of fixture stores that the four stubs index into. Lets a test
    build a self-contained world inline, or load one from disk for a full run."""

    tasks: dict[str, Any]
    persons: dict[str, Any]
    companies: dict[str, Any]
    usage: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "World":
        data = json.loads(Path(path).read_text())
        return cls(
            tasks=data.get("tasks", {}),
            persons=data.get("persons", {}),
            companies=data.get("companies", {}),
            usage=data.get("usage", {}),
        )


class _MissingFixture(KeyError):
    pass


class StubCrmFetcher:
    def __init__(self, world: World) -> None:
        self._world = world

    def read(self, task_id: str) -> dict[str, Any]:
        if task_id not in self._world.tasks:
            raise _MissingFixture(f"no task fixture for {task_id!r}")
        # return a copy so callers can't mutate the fixture store
        return json.loads(json.dumps(self._world.tasks[task_id]))


class StubPersonFetcher:
    def __init__(self, world: World) -> None:
        self._world = world

    def enrich(self, person_ref: dict[str, Any]) -> dict[str, Any]:
        key = person_ref.get("email") or person_ref.get("name") or ""
        record = self._world.persons.get(key)
        if record is None:
            # unknown person: return a thin, honest record rather than inventing
            return {"email": person_ref.get("email"), "name": person_ref.get("name"),
                    "found": False}
        out = json.loads(json.dumps(record))
        out["found"] = True
        return out


class StubCompanyFetcher:
    def __init__(self, world: World) -> None:
        self._world = world

    def enrich(self, domain: str) -> dict[str, Any]:
        record = self._world.companies.get(domain)
        if record is None:
            return {"domain": domain, "found": False}
        out = json.loads(json.dumps(record))
        out["found"] = True
        return out


class StubUsageFetcher:
    def __init__(self, world: World) -> None:
        self._world = world

    def query(self, ref: Any) -> dict[str, Any]:
        # accept either an account id (str) or the CRM record (dict), matching the
        # real fetcher whose resolution needs the record
        account_ref = ref if isinstance(ref, str) else (ref or {}).get("account_ref")
        record = self._world.usage.get(account_ref)
        if record is None:
            return {"account_ref": account_ref, "found": False, "roster": []}
        out = json.loads(json.dumps(record))
        out["found"] = True
        return out
