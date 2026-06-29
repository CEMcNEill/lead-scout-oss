"""Seams the shell depends on: tool provider, staging sink, notifier, clock.

Each is a narrow interface with a Phase 1 stub. The tool provider resolves the
rep-scoped fetchers, voice profile, and exemplars; the staging sink is where a
clean draft lands (a local file now, a real Gmail draft in Phase 1.5); the
notifier is the Slack post (a no-op now); the clock makes time and ids injectable
for deterministic tests.
"""

from __future__ import annotations

import itertools
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from shared.contracts import Draft, LeadRun, RepConfig
from shared.tools.fetchers import (
    CompanyFetcher,
    CrmFetcher,
    PersonFetcher,
    StubCompanyFetcher,
    StubCrmFetcher,
    StubPersonFetcher,
    StubUsageFetcher,
    UsageFetcher,
    World,
)


class ToolProvider(Protocol):
    def fetchers(
        self, rep_config: RepConfig
    ) -> tuple[CrmFetcher, PersonFetcher, CompanyFetcher, UsageFetcher]: ...

    def voice(self, rep_config: RepConfig) -> tuple[str, str]:
        """(voice_profile_text, voice_profile_version)."""
        ...

    def exemplars(self, rep_config: RepConfig, lead_type: str) -> list[str]: ...


class StagingSink(Protocol):
    def stage(self, run_id: str, rep_config: RepConfig, draft: Draft) -> str:
        """Stage a clean draft for review; return a reference. Phase 1: a file
        path. Phase 1.5: a Gmail draft id."""
        ...


class Notifier(Protocol):
    def notify(self, run: LeadRun, rep_config: RepConfig) -> str | None:
        """Post the review card (Slack DM in Phase 1.5). Returns the thread ref
        (parent message ts)."""
        ...


class Clock(Protocol):
    def now(self) -> str: ...
    def new_run_id(self) -> str: ...


# --- Phase 1 stubs --------------------------------------------------------


class StubToolProvider:
    """Fixture-backed provider: stub fetchers over a World, a static voice doc,
    and an exemplar bank keyed by lead_type."""

    def __init__(
        self,
        world: World,
        *,
        voice_profile: str,
        voice_version: str = "v1",
        exemplar_bank: dict[str, list[str]] | None = None,
    ) -> None:
        self._world = world
        self._voice = voice_profile
        self._voice_version = voice_version
        self._bank = exemplar_bank or {}

    def fetchers(self, rep_config: RepConfig):
        return (
            StubCrmFetcher(self._world),
            StubPersonFetcher(self._world),
            StubCompanyFetcher(self._world),
            StubUsageFetcher(self._world),
        )

    def voice(self, rep_config: RepConfig) -> tuple[str, str]:
        return self._voice, self._voice_version

    def exemplars(self, rep_config: RepConfig, lead_type: str) -> list[str]:
        return self._bank.get(lead_type, [])


class CompositeToolProvider:
    """A provider assembled from explicit fetchers, so some can be real and others
    stubbed. Phase 1 Salesforce integration uses a real CRM fetcher here while
    person/company/usage stay stubbed."""

    def __init__(
        self,
        *,
        crm_fetcher: CrmFetcher,
        person_fetcher: PersonFetcher,
        company_fetcher: CompanyFetcher,
        usage_fetcher: UsageFetcher,
        voice_profile: str,
        voice_version: str = "v1",
        exemplar_bank: dict[str, list[str]] | None = None,
    ) -> None:
        self._fetchers = (crm_fetcher, person_fetcher, company_fetcher, usage_fetcher)
        self._voice = voice_profile
        self._voice_version = voice_version
        self._bank = exemplar_bank or {}

    def fetchers(self, rep_config: RepConfig):
        return self._fetchers

    def voice(self, rep_config: RepConfig) -> tuple[str, str]:
        return self._voice, self._voice_version

    def exemplars(self, rep_config: RepConfig, lead_type: str) -> list[str]:
        return self._bank.get(lead_type, [])


def stub_research_fetchers() -> tuple:
    """The three research fetchers as stubs over an empty world, for when only the
    CRM read is real. Unknown people/companies/accounts return honest thin records
    (found: false), so the synthesis tools simply assert little."""
    empty = World(tasks={}, persons={}, companies={}, usage={})
    return (
        StubPersonFetcher(empty),
        StubCompanyFetcher(empty),
        StubUsageFetcher(empty),
    )


class FilesystemStagingSink:
    """Writes the staged draft to a directory as JSON. Stands in for Gmail draft
    staging until Phase 1.5."""

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def stage(self, run_id: str, rep_config: RepConfig, draft: Draft) -> str:
        path = self._dir / f"{run_id}.json"
        path.write_text(json.dumps({"rep_id": rep_config.rep_id, "draft": draft.to_dict()},
                                   indent=2))
        return str(path)


class NullStagingSink:
    def stage(self, run_id: str, rep_config: RepConfig, draft: Draft) -> str:
        return f"memory://{run_id}"


class NullNotifier:
    def notify(self, run: LeadRun, rep_config: RepConfig) -> str | None:
        return None


class RealClock:
    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def new_run_id(self) -> str:
        return f"run_{uuid.uuid4().hex[:12]}"


class FixedClock:
    """Deterministic clock for tests: fixed timestamp, counted run ids."""

    def __init__(self, ts: str = "2026-06-28T12:00:00+00:00", start: int = 1) -> None:
        self._ts = ts
        self._counter = itertools.count(start)

    def now(self) -> str:
        return self._ts

    def new_run_id(self) -> str:
        return f"run_{next(self._counter)}"
