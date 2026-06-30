"""Salesforce integration tests against recorded fixtures (Contact-based model).

This org has no Lead objects: a "lead" is a Task related to a Contact. The poll
selects open Tasks whose Who is a Contact; the CRM read is two queries (the Task,
then the Contact and its Account). A RecordedTransport replays recorded SF JSON,
matched by SOQL substring. No live API.
"""

import json
import urllib.parse
from pathlib import Path

import pytest

from engine.salesforce import (
    SalesforceClient,
    SalesforceCrmFetcher,
    SalesforceTaskSource,
    SfFieldMap,
)
from engine.sf_auth import HttpError, InMemorySecretStore, SalesforceAuth
from shared.contracts import RepConfig

FIX = Path(__file__).resolve().parent / "fixtures" / "salesforce"
REP = RepConfig(
    rep_id="rep_chris", sf_user_id="0055000000REPUSER", sf_credential_ref="kc",
    gmail_account="chris.m@posthog.com", voice_profile_ref="v", signature="Chris",
    slack_post_target="U1", budget_cap_usd=50.0,
)


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


class RecordedTransport:
    """Replays recorded SF responses. post_form returns the recorded token;
    get_json matches a recorded response by SOQL substring or, for pagination, by
    URL path substring. Optionally fails the first GET with a 401."""

    def __init__(self, query_map, page_map=None, *, fail_first_get_status=None):
        self.token = _load("token.json")
        self.query_map = query_map  # list of (soql_substring, response)
        self.page_map = page_map or {}
        self._fail_status = fail_first_get_status
        self._failed = False
        self.posts = 0
        self.gets = 0

    def post_form(self, url, data):
        self.posts += 1
        return self.token

    def get_json(self, url, headers):
        self.gets += 1
        if self._fail_status is not None and not self._failed:
            self._failed = True
            raise HttpError(self._fail_status, "session expired", url)
        if "?q=" in url:
            soql = urllib.parse.unquote(url.split("?q=", 1)[1])
            for sub, resp in self.query_map:
                if sub in soql:
                    return resp
            raise AssertionError(f"no recorded query response for SOQL: {soql}")
        for sub, resp in self.page_map.items():
            if sub in url:
                return resp
        raise AssertionError(f"no recorded page response for URL: {url}")


def _auth(transport) -> SalesforceAuth:
    store = InMemorySecretStore()
    store.set("chris@posthog.com", "STORED_REFRESH_TOKEN")
    return SalesforceAuth(
        client_id="cid", client_secret="csec", redirect_uri="http://localhost:8765/callback",
        sf_username="chris@posthog.com", secret_store=store, transport=transport,
    )


def _client(transport) -> SalesforceClient:
    return SalesforceClient(_auth(transport), transport=transport)


# --- client ---------------------------------------------------------------


def test_current_user_id_from_token_identity():
    assert _client(RecordedTransport(query_map=[])).current_user_id() == "0055000000REPUSER"


def test_query_follows_pagination():
    transport = RecordedTransport(
        query_map=[("FROM Task", _load("task_poll_page1.json"))],
        page_map={"/query/01g5000000PAGE2": _load("task_poll_page2.json")},
    )
    records = _client(transport).query("SELECT Id FROM Task")
    assert [r["Id"] for r in records] == ["00T0001", "00T0002", "00T0003"]


def test_query_refreshes_on_401_and_retries():
    transport = RecordedTransport(
        query_map=[("FROM Task", _load("task_poll.json"))], fail_first_get_status=401
    )
    records = _client(transport).query("SELECT Id FROM Task")
    assert [r["Id"] for r in records] == ["00T0001", "00T0002"]
    assert transport.gets == 2
    assert transport.posts == 2


def test_non_401_error_propagates():
    transport = RecordedTransport(query_map=[], fail_first_get_status=500)
    with pytest.raises(HttpError):
        _client(transport).query("SELECT Id FROM Task")


# --- task source (Contact-based) ------------------------------------------


def test_poll_returns_open_contact_task_ids():
    captured = {}

    class Capturing(RecordedTransport):
        def get_json(self, url, headers):
            captured["soql"] = urllib.parse.unquote(url.split("?q=", 1)[1])
            return super().get_json(url, headers)

    transport = Capturing(query_map=[("FROM Task WHERE OwnerId", _load("task_poll.json"))])
    ids = SalesforceTaskSource(_client(transport)).poll(REP)
    assert ids == ["00T0001", "00T0002"]
    assert "OwnerId = '0055000000REPUSER'" in captured["soql"]
    assert "Status = 'Open'" in captured["soql"]  # only Open tasks
    assert "Who.Type = 'Contact'" in captured["soql"]  # Contact, not Lead


def test_poll_status_is_configurable():
    captured = {}

    class Capturing(RecordedTransport):
        def get_json(self, url, headers):
            import urllib.parse
            captured["soql"] = urllib.parse.unquote(url.split("?q=", 1)[1])
            return super().get_json(url, headers)

    transport = Capturing(query_map=[("FROM Task WHERE OwnerId", _load("task_poll.json"))])
    SalesforceTaskSource(_client(transport), status="In Progress").poll(REP)
    assert "Status = 'In Progress'" in captured["soql"]


def test_poll_excludes_outbound_by_default():
    captured = {}

    class Capturing(RecordedTransport):
        def get_json(self, url, headers):
            captured["soql"] = urllib.parse.unquote(url.split("?q=", 1)[1])
            return super().get_json(url, headers)

    transport = Capturing(query_map=[("FROM Task WHERE OwnerId", _load("task_poll.json"))])
    SalesforceTaskSource(_client(transport)).poll(REP)
    assert "NOT Subject LIKE '[lemlist]%'" in captured["soql"]
    assert "NOT Subject LIKE '[slack - outbound]%'" in captured["soql"]


def test_poll_includes_outbound_when_enabled():
    captured = {}

    class Capturing(RecordedTransport):
        def get_json(self, url, headers):
            captured["soql"] = urllib.parse.unquote(url.split("?q=", 1)[1])
            return super().get_json(url, headers)

    transport = Capturing(query_map=[("FROM Task WHERE OwnerId", _load("task_poll.json"))])
    SalesforceTaskSource(_client(transport), include_outbound=True).poll(REP)
    assert "NOT Subject LIKE" not in captured["soql"]


def test_poll_applies_extra_where_filter():
    captured = {}

    class Capturing(RecordedTransport):
        def get_json(self, url, headers):
            captured["soql"] = urllib.parse.unquote(url.split("?q=", 1)[1])
            return super().get_json(url, headers)

    transport = Capturing(query_map=[("FROM Task WHERE OwnerId", _load("task_poll.json"))])
    source = SalesforceTaskSource(_client(transport), extra_where="Type = 'Lead'")
    source.poll(REP)
    assert "(Type = 'Lead')" in captured["soql"]


# --- CRM fetcher mapping (Task -> Contact -> Account) ---------------------


def _crm(transport, field_map=None) -> SalesforceCrmFetcher:
    return SalesforceCrmFetcher(_client(transport), field_map=field_map)


def test_crm_read_maps_contact_and_account():
    transport = RecordedTransport(query_map=[
        ("FROM Task WHERE Id = '00T0001'", _load("task_inbound.json")),
        ("FROM Contact WHERE Id = '00C0001'", _load("contact_inbound.json")),
    ])
    rec = _crm(transport).read("00T0001")
    assert rec["lead"]["name"] == "Sam Rivera"
    assert rec["lead"]["email"] == "sam@acme.com"
    assert rec["lead"]["domain"] == "acme.com"  # from email
    assert rec["lead"]["company"] == "Acme"  # from related Account
    assert rec["lead"]["lead_source"] == "Contact sales form"
    assert rec["lead"]["owner_other_rep"] is False
    assert rec["inbound_message"].startswith("Our funnels keep breaking")
    assert rec["account_ref"] == "001ACME000000001"  # defaults to AccountId
    assert rec["trigger"] is None


def test_crm_read_competitor_domain():
    transport = RecordedTransport(query_map=[
        ("FROM Task WHERE Id = '00T0002'", _load("task_competitor.json")),
        ("FROM Contact WHERE Id = '00C0002'", _load("contact_competitor.json")),
    ])
    rec = _crm(transport).read("00T0002")
    assert rec["lead"]["domain"] == "mixpanel.com"


def test_crm_read_dnc_owner_other_rep_and_no_message():
    transport = RecordedTransport(query_map=[
        ("FROM Task WHERE Id = '00T0003'", _load("task_dnc.json")),
        ("FROM Contact WHERE Id = '00C0003'", _load("contact_dnc.json")),
    ])
    rec = _crm(transport).read("00T0003")
    assert rec["lead"]["do_not_contact"] is True
    assert rec["lead"]["owner_other_rep"] is True
    assert rec["lead"]["company"] == "Beta"
    assert rec["lead"]["domain"] == "beta.io"  # from email (account website null)
    assert "inbound_message" not in rec


def test_crm_read_custom_trigger_and_account_fields():
    field_map = SfFieldMap(
        trigger_task_field="Trigger__c", account_ref_contact_field="PostHog_Account_Id__c"
    )
    transport = RecordedTransport(query_map=[
        ("FROM Task WHERE Id = '00T0004'", _load("task_plg.json")),
        ("FROM Contact WHERE Id = '00C0004'", _load("contact_plg.json")),
    ])
    rec = _crm(transport, field_map).read("00T0004")
    assert rec["trigger"] == "big_fish_on_free"  # from Task custom field
    assert rec["account_ref"] == "acct_acme"  # from Contact custom field
    assert rec["lead"]["lead_source"] == "Product-led"


def test_custom_fields_appear_in_soql():
    captured = []

    class Capturing(RecordedTransport):
        def get_json(self, url, headers):
            captured.append(urllib.parse.unquote(url.split("?q=", 1)[1]))
            return super().get_json(url, headers)

    field_map = SfFieldMap(
        trigger_task_field="Trigger__c", account_ref_contact_field="PostHog_Account_Id__c"
    )
    transport = Capturing(query_map=[
        ("FROM Task WHERE Id = '00T0004'", _load("task_plg.json")),
        ("FROM Contact WHERE Id = '00C0004'", _load("contact_plg.json")),
    ])
    _crm(transport, field_map).read("00T0004")
    task_soql = next(s for s in captured if "FROM Task" in s)
    contact_soql = next(s for s in captured if "FROM Contact" in s)
    assert "Trigger__c" in task_soql
    assert "Account.Name" in contact_soql
    assert "PostHog_Account_Id__c" in contact_soql


def test_active_sequence_unmapped_defaults_false_and_mapped_field_in_soql():
    captured = []

    class Capturing(RecordedTransport):
        def get_json(self, url, headers):
            captured.append(urllib.parse.unquote(url.split("?q=", 1)[1]))
            return super().get_json(url, headers)

    # unmapped: no field queried, lead is not flagged in an active sequence
    plain = RecordedTransport(query_map=[
        ("FROM Task WHERE Id = '00T0004'", _load("task_plg.json")),
        ("FROM Contact WHERE Id = '00C0004'", _load("contact_plg.json")),
    ])
    rec = _crm(plain).read("00T0004")
    assert rec["lead"]["active_sequence"] is False

    # mapped: the org's sequence field is added to the Contact query
    field_map = SfFieldMap(active_sequence_contact_field="In_Active_Sequence__c")
    transport = Capturing(query_map=[
        ("FROM Task WHERE Id = '00T0004'", _load("task_plg.json")),
        ("FROM Contact WHERE Id = '00C0004'", _load("contact_plg.json")),
    ])
    _crm(transport, field_map).read("00T0004")
    contact_soql = next(s for s in captured if "FROM Contact" in s)
    assert "In_Active_Sequence__c" in contact_soql


def test_missing_task_raises():
    empty = {"totalSize": 0, "done": True, "records": []}
    transport = RecordedTransport(query_map=[("FROM Task WHERE Id = '00TZZZZ'", empty)])
    with pytest.raises(KeyError):
        _crm(transport).read("00TZZZZ")


# --- a recorded SF contact-task flowing through the shell ------------------


def test_recorded_sf_lead_runs_through_shell(tmp_path):
    """The real CRM fetcher (recorded fixtures) feeds the shell end to end, with
    person/company/usage stubbed. Routes by LeadSource + inbound message."""
    from engine.ledger import Ledger
    from engine.providers import CompositeToolProvider, FixedClock, stub_research_fetchers
    from engine.service import assemble_shell
    from shared.contracts import RunStatus, TriggerMeta, TriggerSource
    from shared.model import FakeModel

    transport = RecordedTransport(query_map=[
        ("FROM Task WHERE Id = '00T0001'", _load("task_inbound.json")),
        ("FROM Contact WHERE Id = '00C0001'", _load("contact_inbound.json")),
    ])
    crm_fetcher = _crm(transport)
    person_fetcher, company_fetcher, usage_fetcher = stub_research_fetchers()
    provider = CompositeToolProvider(
        crm_fetcher=crm_fetcher, person_fetcher=person_fetcher,
        company_fetcher=company_fetcher, usage_fetcher=usage_fetcher,
        voice_profile="Plain prose.", exemplar_bank={"inbound": []},
    )
    model = FakeModel({
        "person_research.synthesis:sam@acme.com": "[]",
        "company_research.synthesis:acme.com": "[]",
        # account_ref now defaults to the Contact's AccountId, so inbound also
        # pulls usage; the stub returns thin data and synthesis asserts nothing
        "usage_research.synthesis:001ACME000000001": "[]",
        "use_case_mapping.synthesis": json.dumps([
            {"use_case": "debug broken funnels", "product": "analytics",
             "owner_persona": "Head of Product", "raw_keys": ["message"], "confidence": 0.85}]),
        "inbound.judgment": json.dumps(
            {"disposition": "call", "reasoning": "clear inbound use case (c1)",
             "confidence": 0.8, "claim_refs": ["c1"], "target_email": "sam@acme.com"}),
        "drafter": json.dumps({"subject": "PostHog for funnel debugging",
                               "body": "Saw your funnels keep breaking.", "claims_used": ["c1"]}),
        "factcheck": "[]",
    })

    ledger = Ledger(tmp_path / "l.db")
    shell = assemble_shell(ledger=ledger, inner_model=model, tool_provider=provider,
                           staging_dir=tmp_path / "staged")
    shell.clock = FixedClock()
    run = shell.process_lead_run("00T0001", REP, TriggerMeta(TriggerSource.BATCH, ""))

    assert run.status == RunStatus.STAGED_FOR_REVIEW
    assert run.route.qualifier == "inbound"
    assert run.staged_draft is not None and run.staged_draft.to == "sam@acme.com"
    emails = [c.value for c in run.dossier if c.field == "contact_email"]
    assert emails == ["sam@acme.com"]
    companies = [c.value for c in run.dossier if c.field == "company_name"]
    assert companies == ["Acme"]
    ledger.close()
