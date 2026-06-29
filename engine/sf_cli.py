"""Salesforce access via the Salesforce CLI (`sf`).

This is the no-app-setup path: you run `sf org login web` once (a real browser
login that uses Salesforce's own connected app), and the engine talks to your org
by shelling out to the CLI, which owns auth and token refresh. No External Client
App, no client id/secret, no Keychain code.

SfCliClient satisfies the same QueryClient surface as the REST client, so the
task source and CRM fetcher are reused unchanged. The subprocess boundary is
behind a CommandRunner so tests replay recorded CLI JSON instead of calling `sf`.

Trade-off vs. the spec's External Client App: simpler to start and single-rep
local, but it leans on the CLI being installed and logged in. Revisit the ECA /
JWT path for the multi-rep, cloud phase.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Protocol

from engine.salesforce import _soql_escape

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class SfCliError(RuntimeError):
    pass


class CommandRunner(Protocol):
    def run(self, args: list[str]) -> dict[str, Any]:
        """Run an `sf` command (args after the `sf`) and return parsed JSON."""
        ...


class SubprocessRunner:
    def __init__(self, executable: str = "sf") -> None:
        self._exe = executable

    def run(self, args: list[str]) -> dict[str, Any]:
        if shutil.which(self._exe) is None:
            raise SfCliError(
                f"the Salesforce CLI ({self._exe!r}) is not installed. Install it "
                "(brew install salesforcedx, or npm i -g @salesforce/cli) and run "
                "`sf org login web`."
            )
        # disable colorized output so the JSON parses; the CLI otherwise emits
        # ANSI escape codes even when its output is piped
        env = {**os.environ, "NO_COLOR": "1", "SF_NO_COLOR": "1", "FORCE_COLOR": "0"}
        # SF_API_VERSION (set for the REST path, e.g. "v60.0") must not leak into
        # the CLI, which reads it and rejects the 'v' prefix. Let the CLI default.
        env.pop("SF_API_VERSION", None)
        env.pop("SFDX_API_VERSION", None)
        proc = subprocess.run(
            [self._exe, *args], capture_output=True, text=True, env=env
        )
        stdout = _ANSI.sub("", proc.stdout)  # strip any residual color codes
        if not stdout.strip():
            raise SfCliError(f"`sf {' '.join(args)}` produced no output: {proc.stderr.strip()}")
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SfCliError(f"could not parse `sf {' '.join(args)}` output: {exc}") from exc


class SfCliClient:
    """A QueryClient backed by the Salesforce CLI."""

    def __init__(self, *, target_org: str | None = None, runner: CommandRunner | None = None) -> None:
        self._target_org = target_org
        self._runner = runner or SubprocessRunner()
        self._username: str | None = None
        self._user_id: str | None = None

    def _run(self, args: list[str]) -> dict[str, Any]:
        cmd = [*args, "--json"]
        if self._target_org:
            cmd += ["--target-org", self._target_org]
        out = self._runner.run(cmd)
        if out.get("status", 0) != 0:
            raise SfCliError(out.get("message") or f"sf command failed: {out}")
        return out

    def username(self) -> str:
        if self._username is None:
            result = self._run(["org", "display"]).get("result", {})
            username = result.get("username")
            if not username:
                raise SfCliError("`sf org display` returned no username; run `sf org login web`")
            self._username = username
        return self._username

    def query(self, soql: str) -> list[dict[str, Any]]:
        out = self._run(["data", "query", "--query", soql])
        return out.get("result", {}).get("records", [])

    def current_user_id(self) -> str:
        if self._user_id is None:
            username = self.username()
            rows = self.query(
                f"SELECT Id FROM User WHERE Username = '{_soql_escape(username)}'"
            )
            if not rows:
                raise SfCliError(f"no User found for username {username!r}")
            self._user_id = rows[0]["Id"]
        return self._user_id

    def update_record(self, sobject: str, record_id: str, fields: dict[str, Any]) -> None:
        """Update a record's fields. Used to write back a human-confirmed account
        correction (e.g. the right PostHog org id on the Task)."""
        values = " ".join(f"{k}={v}" for k, v in fields.items())
        self._run(["data", "update", "record", "--sobject", sobject,
                   "--record-id", record_id, "--values", values])


def build_cli_client() -> SfCliClient:
    import os

    return SfCliClient(target_org=os.environ.get("SF_TARGET_ORG") or None)
