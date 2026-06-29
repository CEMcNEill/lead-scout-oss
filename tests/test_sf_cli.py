"""Salesforce CLI-backed client tests, against recorded `sf` JSON.

A FakeRunner replays recorded CLI output (matched by subcommand and SOQL), so the
suite never invokes the real `sf` binary or a live org. Proves the CLI client
satisfies the QueryClient surface the task source and CRM fetcher depend on.
"""

import json
from pathlib import Path

import pytest

from engine.salesforce import SalesforceCrmFetcher, SalesforceTaskSource
from engine.sf_cli import SfCliClient, SfCliError
from shared.contracts import RepConfig

FIX = Path(__file__).resolve().parent / "fixtures" / "salesforce_cli"
REP = RepConfig(
    rep_id="rep_chris", sf_user_id="0055000000REPUSER", sf_credential_ref="cli",
    gmail_account="chris.m@posthog.com", voice_profile_ref="v", signature="Chris",
    slack_post_target="U1", budget_cap_usd=50.0,
)


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


class FakeRunner:
    """Replays recorded `sf` JSON. Matches org display, and data query by SOQL
    substring. Records the commands it saw for assertions."""

    def __init__(self, query_map, org_display=None):
        self.query_map = query_map  # list of (soql_substring, response)
        self.org_display = org_display or _load("org_display.json")
        self.commands: list[list[str]] = []

    def run(self, args):
        self.commands.append(list(args))
        if "org" in args and "display" in args:
            return self.org_display
        if "data" in args and "query" in args:
            soql = args[args.index("--query") + 1]
            for sub, resp in self.query_map:
                if sub in soql:
                    return resp
            raise AssertionError(f"no recorded CLI response for SOQL: {soql}")
        raise AssertionError(f"unexpected sf command: {args}")


def _client(runner) -> SfCliClient:
    return SfCliClient(runner=runner)


def test_username_from_org_display():
    client = _client(FakeRunner(query_map=[]))
    assert client.username() == "chris.m@posthog.com"


def test_current_user_id_resolves_via_user_query():
    runner = FakeRunner(query_map=[("FROM User WHERE Username", _load("query_user.json"))])
    client = _client(runner)
    assert client.current_user_id() == "0055000000REPUSER"
    # cached: a second call does not re-query
    before = len(runner.commands)
    assert client.current_user_id() == "0055000000REPUSER"
    assert len(runner.commands) == before


def test_query_returns_records():
    runner = FakeRunner(query_map=[("FROM Task", _load("query_poll.json"))])
    records = _client(runner).query("SELECT Id FROM Task")
    assert [r["Id"] for r in records] == ["00T0001", "00T0002"]


def test_json_flag_and_target_org_are_passed():
    runner = FakeRunner(query_map=[("FROM Task", _load("query_poll.json"))])
    SfCliClient(runner=runner, target_org="posthog").query("SELECT Id FROM Task")
    cmd = runner.commands[-1]
    assert "--json" in cmd
    assert cmd[cmd.index("--target-org") + 1] == "posthog"


def test_failed_status_raises():
    class FailRunner:
        def run(self, args):
            return {"status": 1, "message": "No authorization found, run sf org login web"}

    with pytest.raises(SfCliError):
        _client(FailRunner()).query("SELECT Id FROM Task")


def test_task_source_reuses_cli_client():
    captured = {}

    class Capturing(FakeRunner):
        def run(self, args):
            if "data" in args and "query" in args:
                captured["soql"] = args[args.index("--query") + 1]
            return super().run(args)

    runner = Capturing(query_map=[
        ("FROM User WHERE Username", _load("query_user.json")),
        ("FROM Task WHERE OwnerId", _load("query_poll.json")),
    ])
    ids = SalesforceTaskSource(_client(runner)).poll(REP)
    assert ids == ["00T0001", "00T0002"]
    assert "Who.Type = 'Contact'" in captured["soql"]  # Contact, not Lead


def test_crm_fetcher_reuses_cli_client():
    runner = FakeRunner(query_map=[
        ("FROM User WHERE Username", _load("query_user.json")),
        ("FROM Task WHERE Id = '00T0001'", _load("query_task_inbound.json")),
        ("FROM Contact WHERE Id = '00C0001'", _load("query_contact_inbound.json")),
    ])
    rec = SalesforceCrmFetcher(_client(runner)).read("00T0001")
    assert rec["lead"]["email"] == "sam@acme.com"
    assert rec["lead"]["domain"] == "acme.com"
    assert rec["lead"]["company"] == "Acme"
    assert rec["lead"]["owner_other_rep"] is False
    assert rec["inbound_message"].startswith("Our funnels keep breaking")
