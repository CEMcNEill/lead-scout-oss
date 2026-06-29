"""Nightly slow-loop tests: voice diff + classification, judgment parsing +
disagreement detection, ledger updates, and propose-then-approve markdown output.
Recorded Gmail/Slack clients and a scripted model; no live calls."""

import json
from pathlib import Path

import pytest

from engine.gmail import GmailMessage, RecordedGmailClient
from engine.ledger import Ledger
from engine.loop_slow import SlowLoop
from engine.slack import RecordedSlackClient
from shared.contracts import (
    Disposition,
    DispositionKind,
    Draft,
    LeadRun,
    RepConfig,
    Route,
    RunStatus,
    Target,
    TriggerSource,
)
from shared.model import FakeModel

REPO = Path(__file__).resolve().parent.parent
REP = RepConfig(
    rep_id="rep_chris", sf_user_id="x", sf_credential_ref="cli",
    gmail_account="chris.m@posthog.com", voice_profile_ref="v", signature="Chris",
    slack_post_target="U1", budget_cap_usd=50.0,
)


def _run(task_id, *, to, subject, body, disp=DispositionKind.CALL, thread=None) -> LeadRun:
    return LeadRun(
        id=f"run_{task_id}", task_id=task_id, rep_id="rep_chris",
        trigger_source=TriggerSource.BATCH, ts="2026-06-28T12:00:00Z",
        route=Route(lead_type="inbound", qualifier="inbound"),
        status=RunStatus.STAGED_FOR_REVIEW,
        disposition=Disposition(disposition=disp, reasoning="r", confidence=0.8,
                                claim_refs=["c1"], target=Target(name="x", email=to)),
        staged_draft=Draft(to=to, subject=subject, body=body, angle="x"),
        slack_thread_ref=thread,
    )


def _model() -> FakeModel:
    return FakeModel({
        "slow.classify_edits": json.dumps(
            {"substantive": [], "stylistic": ["casual greeting", "softer ask"]}),
        "slow.voice_proposal": "## Voice\n- Open with a casual greeting.\n- Soften the ask.",
        "slow.parse_reply": json.dumps(
            {"disposition": "self_serve", "rationale": "generic alias, no buying authority"}),
        "slow.rubric_proposal": "## Rubric\n- Treat generic aliases as self_serve absent signal.",
    })


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "l.db")
    yield led
    led.close()


def _slowloop(ledger, gmail, slack, tmp_path, model=None) -> SlowLoop:
    return SlowLoop(
        ledger=ledger, gmail=gmail, slack=slack, model=model or _model(), rep_config=REP,
        proposals_dir=tmp_path / "proposals",
        voice_profile_path=REPO / "config" / "voice" / "chris.md",
        rubric_path=REPO / "config" / "rubric.md",
    )


def test_voice_subloop_diffs_classifies_and_proposes(ledger, tmp_path):
    ledger.insert(_run("A", to="dana@acme.com", subject="PostHog at Acme",
                       body="Saw your funnels. Worth a chat?"))
    gmail = RecordedGmailClient(sent={"dana@acme.com": [
        GmailMessage("m1", "t1", "PostHog at Acme", "dana@acme.com",
                     "Hey Dana, saw your funnels were acting up. Up for a quick chat?", "2026-06-02")
    ]})
    result = _slowloop(ledger, gmail, RecordedSlackClient(), tmp_path).run_nightly("2026-06-29")

    assert result.runs_with_sent == 1
    assert len(result.voice_edits) == 1
    assert result.voice_edits[0].stylistic  # stylistic edits captured
    # ledger updated with the sent copy + diff
    a = ledger.get("run_A")
    assert a.sent_draft is not None and "Hey Dana" in a.sent_draft.body
    assert a.draft_diff and "+Hey Dana" in a.draft_diff
    # proposal written, propose-then-approve (not applied)
    assert result.voice_proposal_path and Path(result.voice_proposal_path).exists()
    assert "PROPOSED voice-profile" in Path(result.voice_proposal_path).read_text()


def test_voice_subloop_no_signal_when_sent_unchanged(ledger, tmp_path):
    body = "Saw your funnels. Worth a chat?"
    ledger.insert(_run("A", to="dana@acme.com", subject="PostHog at Acme", body=body))
    gmail = RecordedGmailClient(sent={"dana@acme.com": [
        GmailMessage("m1", "t1", "PostHog at Acme", "dana@acme.com", body, "2026-06-02")]})
    result = _slowloop(ledger, gmail, RecordedSlackClient(), tmp_path).run_nightly("2026-06-29")
    assert result.runs_with_sent == 1
    assert result.voice_edits == []  # identical send -> no voice signal
    assert ledger.get("run_A").draft_diff == ""
    assert result.voice_proposal_path is None


def test_judgment_subloop_detects_disagreement_and_proposes(ledger, tmp_path):
    ledger.insert(_run("B", to="sam@beta.io", subject="x", body="y",
                       disp=DispositionKind.CALL, thread="171000000.000001"))
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "*New lead* card"}, {"text": "engine reasoning"},
        {"text": "nah, this is self-serve, it's a generic alias"},
    ]})
    result = _slowloop(ledger, RecordedGmailClient(), slack, tmp_path).run_nightly("2026-06-29")

    assert result.runs_with_replies == 1
    assert len(result.disagreements) == 1
    d = result.disagreements[0]
    assert d.llm_disposition == "call" and d.human_disposition == "self_serve"
    b = ledger.get("run_B")
    assert b.human_disposition == "self_serve"
    assert b.human_rationale
    assert result.rubric_proposal_path and "PROPOSED rubric" in Path(result.rubric_proposal_path).read_text()


def test_judgment_agreement_records_but_no_disagreement(ledger, tmp_path):
    ledger.insert(_run("B", to="sam@beta.io", subject="x", body="y",
                       disp=DispositionKind.SELF_SERVE, thread="171000000.000001"))
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "card"}, {"text": "reasoning"}, {"text": "agreed, self-serve"},
    ]})
    result = _slowloop(ledger, RecordedGmailClient(), slack, tmp_path).run_nightly("2026-06-29")
    assert result.runs_with_replies == 1
    assert result.disagreements == []  # human agrees with llm (both self_serve)
    assert ledger.get("run_B").human_disposition == "self_serve"


class FakeWriter:
    def __init__(self):
        self.updates = []

    def update_record(self, sobject, record_id, fields):
        self.updates.append({"sobject": sobject, "id": record_id, "fields": fields})


def _resolution_claim():
    from shared.contracts import Claim
    return Claim(
        id="cR", field="usage_account_resolution",
        value={"chosen": "001A", "ambiguous": True, "candidates": [
            {"id": "001A", "name": "Acme", "org_id": "old-org", "events_30d": 5},
            {"id": "001B", "name": "Champ Inc", "org_id": "right-org", "events_30d": 154568},
        ]},
        source="usage_research", raw={"resolution": {}}, confidence=1.0)


def _slowloop_w(ledger, slack, tmp_path, writer=None):
    return SlowLoop(
        ledger=ledger, gmail=RecordedGmailClient(), slack=slack, model=_model(), rep_config=REP,
        proposals_dir=tmp_path / "proposals",
        voice_profile_path=REPO / "config" / "voice" / "chris.md",
        rubric_path=REPO / "config" / "rubric.md", sf_writer=writer,
    )


def test_account_correction_written_back_when_rep_picks_a_different_account(ledger, tmp_path):
    run = _run("B", to="sam@beta.io", subject="x", body="y", thread="171000000.000001")
    run.dossier.append(_resolution_claim())
    ledger.insert(run)
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "card"}, {"text": "reasoning"},
        {"text": "the right one is Champ Inc, not the empty one"},
    ]})
    writer = FakeWriter()
    result = _slowloop_w(ledger, slack, tmp_path, writer).run_nightly("2026-06-29")

    assert len(result.account_corrections) == 1
    corr = result.account_corrections[0]
    assert corr.account_id == "001B" and corr.org_id == "right-org" and corr.written is True
    # wrote the corrected PostHog org id back to the Task
    assert writer.updates == [
        {"sobject": "Task", "id": "B", "fields": {"posthog_org_id__c": "right-org"}}
    ]


def test_no_correction_when_rep_confirms_engine_pick(ledger, tmp_path):
    run = _run("B", to="sam@beta.io", subject="x", body="y", thread="171000000.000001")
    run.dossier.append(_resolution_claim())  # chosen is 001A
    ledger.insert(run)
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "card"}, {"text": "reasoning"}, {"text": "yep Acme is right"},
    ]})
    writer = FakeWriter()
    result = _slowloop_w(ledger, slack, tmp_path, writer).run_nightly("2026-06-29")
    assert result.account_corrections == []
    assert writer.updates == []


def test_correction_recorded_without_writer(ledger, tmp_path):
    run = _run("B", to="sam@beta.io", subject="x", body="y", thread="171000000.000001")
    run.dossier.append(_resolution_claim())
    ledger.insert(run)
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "card"}, {"text": "reasoning"}, {"text": "use right-org"},
    ]})
    result = _slowloop_w(ledger, slack, tmp_path, writer=None).run_nightly("2026-06-29")
    assert len(result.account_corrections) == 1
    assert result.account_corrections[0].written is False


def test_updates_only_applies_corrections_without_proposals(ledger, tmp_path):
    run = _run("B", to="sam@beta.io", subject="x", body="y", thread="171000000.000001")
    run.dossier.append(_resolution_claim())
    ledger.insert(run)
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "card"}, {"text": "reasoning"}, {"text": "use Champ Inc"},
    ]})
    writer = FakeWriter()
    result = _slowloop_w(ledger, slack, tmp_path, writer).run_updates_only("2026-06-29")
    # correction applied + override recorded, but no proposals written
    assert len(result.account_corrections) == 1 and writer.updates
    assert result.voice_edits == []
    assert result.voice_proposal_path is None and result.rubric_proposal_path is None


def test_thread_with_no_rep_reply_is_skipped(ledger, tmp_path):
    ledger.insert(_run("B", to="sam@beta.io", subject="x", body="y", thread="171000000.000001"))
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "card"}, {"text": "reasoning"},  # only the engine's own messages
    ]})
    result = _slowloop(ledger, RecordedGmailClient(), slack, tmp_path).run_nightly("2026-06-29")
    assert result.runs_with_replies == 0
    assert result.disagreements == []
    assert slack.reactions == []  # nothing to acknowledge


def test_processed_reply_gets_reaction_and_threaded_ack(ledger, tmp_path):
    ledger.insert(_run("B", to="sam@beta.io", subject="x", body="y",
                       disp=DispositionKind.CALL, thread="171000000.000001"))
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "card"}, {"text": "reasoning"},
        {"text": "nah, self-serve", "ts": "171000000.000002"},  # rep reply, has a ts
    ]})
    result = _slowloop(ledger, RecordedGmailClient(), slack, tmp_path).run_updates_only("2026-06-29")
    # headless reaction
    assert slack.reactions == [
        {"channel": "U1", "timestamp": "171000000.000002", "emoji": "white_check_mark"},
    ]
    # MCP-path threaded ack the agent will post
    assert len(result.acknowledgements) == 1
    ack = result.acknowledgements[0]
    assert ack["task_id"] == "B" and ack["thread_ts"] == "171000000.000001"
    assert ack["message"].startswith("✅ lead-scout:")
    assert "self serve" in ack["message"]  # disagreement: rep's call recorded
    # dedup recorded on the run
    assert ledger.get("run_B").acked_reply_ts == "171000000.000002"


def test_ack_fires_once_not_every_sweep(ledger, tmp_path):
    ledger.insert(_run("B", to="sam@beta.io", subject="x", body="y",
                       disp=DispositionKind.CALL, thread="171000000.000001"))
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "card"}, {"text": "reasoning"},
        {"text": "nah, self-serve", "ts": "171000000.000002"},
    ]})
    _slowloop(ledger, RecordedGmailClient(), slack, tmp_path).run_updates_only("2026-06-29")
    # second sweep over the same unchanged thread: nothing new to acknowledge
    result2 = _slowloop(ledger, RecordedGmailClient(), slack, tmp_path).run_updates_only("2026-06-29")
    assert result2.runs_with_replies == 0
    assert result2.acknowledgements == []
    assert len(slack.reactions) == 1  # not re-reacted


def test_engine_ignores_its_own_ack_message(ledger, tmp_path):
    run = _run("B", to="sam@beta.io", subject="x", body="y",
               disp=DispositionKind.CALL, thread="171000000.000001")
    run.acked_reply_ts = "171000000.000002"  # already acked the rep's reply
    ledger.insert(run)
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "card"}, {"text": "reasoning"},
        {"text": "nah, self-serve", "ts": "171000000.000002"},
        {"text": "✅ lead-scout: Seen and acted on. Recorded your call: self serve.",
         "ts": "171000000.000003"},  # the engine's own ack, posted as the rep
    ]})
    result = _slowloop(ledger, RecordedGmailClient(), slack, tmp_path).run_updates_only("2026-06-29")
    assert result.runs_with_replies == 0  # the ack is filtered; no new rep feedback
    assert result.acknowledgements == []


def test_ack_skips_replies_without_a_ts(ledger, tmp_path):
    # the recorded MCP-path fixtures often omit ts; the ack must not crash or react
    ledger.insert(_run("B", to="sam@beta.io", subject="x", body="y",
                       disp=DispositionKind.CALL, thread="171000000.000001"))
    slack = RecordedSlackClient(thread_messages={"171000000.000001": [
        {"text": "card"}, {"text": "reasoning"}, {"text": "agreed, self-serve"},  # no ts
    ]})
    result = _slowloop(ledger, RecordedGmailClient(), slack, tmp_path).run_updates_only("2026-06-29")
    assert result.runs_with_replies == 1
    assert slack.reactions == []
    assert result.acknowledgements == []  # no ts to dedup on, so no ack emitted
