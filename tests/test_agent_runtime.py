"""Agent-runtime entrypoint tests: poll, process, card, set-thread.

A fake Salesforce client serves the CRM read, the poll, and usage resolution; the
model is scripted. No MCP, no live calls. Clay enrichment is supplied as the
agent would (recorded JSON), or omitted (thin)."""

import json
from pathlib import Path

import pytest

from engine import agent_runtime as ar
from engine.ledger import Ledger
from shared.contracts import (
    Claim, Disposition, DispositionKind, Draft, LeadRun, RepConfig, Route, RunStatus,
    Target, TriggerSource,
)
from shared.model import FakeModel

REP = RepConfig(rep_id="rep_chris", sf_user_id="005ME", sf_credential_ref="cli",
                gmail_account="g", voice_profile_ref="v", signature="Chris",
                slack_post_target="U1", budget_cap_usd=50.0)


class FakeSfClient:
    """Serves the queries the CRM fetcher, task source, and usage fetcher make."""

    def current_user_id(self):
        return "005ME"

    def query(self, soql):
        if "FROM Task WHERE OwnerId" in soql:
            return [{"Id": "T1"}]
        if "FROM Task WHERE Id = 'T1'" in soql:
            return [{"Id": "T1", "Subject": "[Default Contact Form] sam@acme.com",
                     "Description": "We want SSO before rolling out.", "WhoId": "C1",
                     "posthog_org_id__c": "org1"}]
        if "FROM Contact WHERE Id = 'C1'" in soql:
            return [{"Id": "C1", "Name": "- sam@acme.com", "Email": "sam@acme.com",
                     "Title": None, "LeadSource": "Contact sales form", "OwnerId": "005ME",
                     "AccountId": "A1", "Posthog_Org_ID__c": "org1",
                     "Account": {"Name": "Acme", "Website": "https://acme.com"}}]
        if "FROM Contact WHERE Email LIKE" in soql:
            return [{"AccountId": "A1"}]
        if "FROM Account WHERE Posthog_Org_ID__c" in soql:
            return [{"Id": "A1"}]
        if "FROM Account WHERE Name" in soql:
            return [{"Id": "A1"}]
        if "FROM Account WHERE Id IN" in soql:
            return [{"Id": "A1", "Name": "Acme", "Posthog_Org_ID__c": "org1",
                     "posthog_total_events_30d__c": 5000,
                     "posthog_products_30d__c": "analytics"}]
        raise AssertionError(f"unexpected SOQL: {soql}")


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "l.db")
    yield led
    led.close()


def _model() -> FakeModel:
    return FakeModel({
        "person_research.synthesis:sam@acme.com": "[]",
        "company_research.synthesis:acme.com": "[]",
        "usage_research.synthesis:A1": json.dumps(
            [{"field": "monthly_event_volume", "value": 5000, "raw_keys": ["events_30d"],
              "confidence": 0.9}]),
        "use_case_mapping.synthesis": json.dumps(
            [{"use_case": "SSO rollout", "product": "analytics", "owner_persona": "IT",
              "raw_keys": ["message"], "confidence": 0.8}]),
        "inbound.judgment": json.dumps(
            {"disposition": "call", "reasoning": "SSO ask (c1)", "confidence": 0.8,
             "claim_refs": ["c1"], "target_email": "sam@acme.com"}),
        "drafter": json.dumps({"subject": "SSO at Acme", "body": "Saw your SSO note.",
                               "claims_used": ["c1"]}),
        "factcheck": "[]",
    })


# --- pure pieces ----------------------------------------------------------


def test_enrich_name_derives_from_email_for_placeholder():
    assert ar._enrich_name({"name": "- sam@acme.com", "email": "sam.r@acme.com"}) == "Sam R"
    assert ar._enrich_name({"name": "Dana Lopez", "email": "d@x.com"}) == "Dana Lopez"


def test_clay_caller_built_from_files(tmp_path):
    cf = tmp_path / "company.json"
    cf.write_text(json.dumps({"companies": {"acme.com": {"name": "Acme"}}, "contacts": []}))
    record = {"lead": {"email": "sam@acme.com", "domain": "acme.com", "name": "- sam@acme.com"}}
    caller = ar._clay_caller(record, str(cf), None)
    assert caller.enrich_company("acme.com")["companies"]["acme.com"]["name"] == "Acme"


# --- poll + process -------------------------------------------------------


def test_poll_targets_lists_new_tasks_with_enrichment_targets(ledger):
    targets = ar.poll_targets(FakeSfClient(), ledger, REP)
    assert targets == [{"task_id": "T1", "company_domain": "acme.com",
                        "contact_name": "Sam", "contact_email": "sam@acme.com"}]
    # once in the ledger, poll skips it
    ledger.insert(LeadRun(id="r", task_id="T1", rep_id="rep_chris",
                          trigger_source=TriggerSource.BATCH, ts="t",
                          route=Route("inbound", "inbound"), status=RunStatus.STAGED_FOR_REVIEW))
    assert ar.poll_targets(FakeSfClient(), ledger, REP) == []


def test_process_one_qualifies_and_writes_ledger(ledger, monkeypatch):
    monkeypatch.setattr(ar, "default_rep_config", lambda: REP)
    run = ar.process_one("T1", client=FakeSfClient(), ledger=ledger, model=_model())
    assert run.status == RunStatus.STAGED_FOR_REVIEW
    assert run.route.qualifier == "inbound"
    assert run.staged_draft is not None and run.staged_draft.to == "sam@acme.com"
    # persisted for dedup
    assert ledger.get_by_task("T1").disposition.disposition == DispositionKind.CALL
    # usage came through from SF resolution
    assert any(c.field == "monthly_event_volume" for c in run.dossier)


# --- card + thread --------------------------------------------------------


def _staged_run() -> LeadRun:
    return LeadRun(
        id="r1", task_id="T1", rep_id="rep_chris", trigger_source=TriggerSource.BATCH,
        ts="t", route=Route("inbound", "inbound"), status=RunStatus.STAGED_FOR_REVIEW,
        dossier=[Claim(id="c1", field="company_name", value="Acme", source="crm_context",
                       raw={"x": 1}, confidence=1.0)],
        disposition=Disposition(DispositionKind.CALL, "SSO ask", 0.8, ["c1"],
                                Target(name="Sam", email="sam@acme.com")),
        staged_draft=Draft(to="sam@acme.com", subject="SSO", body="hi", angle="x"),
    )


def test_render_card_sets_ref_and_returns_text(ledger):
    ledger.insert(_staged_run())
    out = ar.render_card(ledger, "T1", "https://mail.google.com/d/abc")
    assert "Acme" in out["card"]
    assert "abc" in out["card"]  # the draft link
    assert ledger.get_by_task("T1").staged_draft_ref == "https://mail.google.com/d/abc"


def test_set_thread_records_ref(ledger):
    ledger.insert(_staged_run())
    ar.set_thread(ledger, "T1", "171.0001")
    assert ledger.get_by_task("T1").slack_thread_ref == "171.0001"


def test_voice_corpus_dumps_staged_drafts(ledger):
    ledger.insert(_staged_run())
    rows = ar.voice_corpus(ledger, REP)
    assert len(rows) == 1
    row = rows[0]
    assert row["email"] == "sam@acme.com"
    assert row["company"] == "Acme"
    assert row["play"] == "inbound"
    assert row["stagedBody"] == "hi"  # the staged body (pre-edit), for the diff


# --- nightly slow loop ----------------------------------------------------


def test_slow_targets_lists_what_to_fetch(ledger):
    run = _staged_run()
    run.slack_thread_ref = "171.0001"
    ledger.insert(run)
    targets = ar.slow_targets(ledger, REP)
    assert targets == [{"task_id": "T1", "draft_to": "sam@acme.com",
                        "draft_subject": "SSO", "slack_thread_ref": "171.0001"}]


def test_slow_run_drives_the_loop_from_agent_data(ledger, tmp_path):
    run = _staged_run()
    run.slack_thread_ref = "171.0001"
    ledger.insert(run)
    data = {
        "sent": {"sam@acme.com": [{"subject": "SSO", "body": "hi, edited and sent",
                                   "date": "2026-06-02"}]},
        "threads": {"171.0001": [{"text": "card"}, {"text": "reasoning"},
                                 {"text": "agree, this is a call"}]},
    }
    model = FakeModel({
        "slow.classify_edits": json.dumps({"substantive": [], "stylistic": ["added a greeting"]}),
        "slow.voice_proposal": "## Voice\n- Add a greeting.",
        "slow.parse_reply": json.dumps({"disposition": "call", "rationale": "agree"}),
    })

    class FakeWriter:
        def __init__(self): self.updates = []
        def update_record(self, *a): self.updates.append(a)

    result = ar.slow_run(ledger, data, client=FakeWriter(), model=model, rep_config=REP,
                         stamp="2026-06-29", repo_root=Path(__file__).resolve().parent.parent)
    assert len(result.voice_edits) == 1  # staged draft vs edited sent -> a voice signal
    assert result.disagreements == []  # rep agreed (call == call)
    back = ledger.get_by_task("T1")
    assert back.sent_draft is not None and "edited and sent" in back.sent_draft.body


# --- heartbeat + status ---------------------------------------------------


def _run(task_id: str, status: RunStatus, ts: str) -> LeadRun:
    return LeadRun(id=task_id, task_id=task_id, rep_id="rep_chris",
                   trigger_source=TriggerSource.BATCH, ts=ts,
                   route=Route("inbound", "inbound"), status=status)


def test_window_counts_filters_by_since_and_classifies_status(ledger):
    ledger.insert(_run("A", RunStatus.STAGED_FOR_REVIEW, "2026-06-29T10:00:00+00:00"))
    ledger.insert(_run("B", RunStatus.BLOCKED, "2026-06-29T10:05:00+00:00"))
    ledger.insert(_run("C", RunStatus.ERROR, "2026-06-29T10:10:00+00:00"))
    ledger.insert(_run("D", RunStatus.STAGED_FOR_REVIEW, "2026-06-29T08:00:00+00:00"))  # pre-window
    counts = ar._window_counts(ledger, "2026-06-29T09:00:00+00:00", REP)
    assert counts == {"processed": 3, "staged": 1, "blocked": 1, "errors": 1}
    assert ar._window_counts(ledger, None, REP)["processed"] == 4  # no window = all


def test_heartbeat_message_silent_on_idle_speaks_on_activity_and_failure():
    idle = {"ok": True, "exit_code": 0, "processed": 0, "staged": 0, "blocked": 0, "errors": 0}
    assert ar.heartbeat_message(idle) == ""  # successful but did nothing -> silent
    active = {"ok": True, "exit_code": 0, "processed": 2, "staged": 1, "blocked": 0, "errors": 0}
    assert ar.heartbeat_message(active) == "processed 2, staged 1"
    failed = {"ok": False, "exit_code": 1, "processed": 0, "staged": 0, "blocked": 0, "errors": 0}
    msg = ar.heartbeat_message(failed)
    assert "failed" in msg.lower() and "err.log" in msg


def test_write_heartbeat_persists_file_and_status_reads_it(ledger):
    ledger.insert(_run("A", RunStatus.STAGED_FOR_REVIEW, "2026-06-29T10:00:00+00:00"))
    hb = ar.write_heartbeat(ledger, REP, "2026-06-29T09:00:00+00:00", 0)
    assert hb["processed"] == 1 and hb["staged"] == 1 and hb["ok"] is True
    assert ar._heartbeat_path(ledger).exists()
    report = ar.status_report(ledger, REP)
    assert report["heartbeat"]["exit_code"] == 0
    assert report["ledger_runs_total"] == 1
    text = ar.render_status(report)
    assert "last sweep" in text and "ok" in text
