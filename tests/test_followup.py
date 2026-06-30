"""Phase 2b: follow-up state detection in the slow loop (observe-only).

Reply-gate matrix, sent-touch accounting, frozen-clock due windows, and the
fail-closed rules: a follow-up is scheduled only when a real send was matched, the
thread was readable, and no reply was seen. Recorded Gmail/Slack, no live calls.
"""

from pathlib import Path

import pytest

from engine.gmail import GmailMessage, RecordedGmailClient
from engine.ledger import Ledger
from engine.loop_slow import SlowLoop, SlowLoopResult
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
    slack_post_target="", budget_cap_usd=50.0,
)
CADENCE = {"startup_rolloff": [4, 7]}
SENT_DATE = "Mon, 01 Jun 2026 10:00:00 +0000"


def _run(task_id="t1", *, disp=DispositionKind.CALL, lead_type="startup_rolloff") -> LeadRun:
    return LeadRun(
        id=f"run_{task_id}", task_id=task_id, rep_id="rep_chris",
        trigger_source=TriggerSource.BATCH, ts="2026-05-30T12:00:00Z",
        route=Route(lead_type=lead_type, qualifier=lead_type),
        status=RunStatus.STAGED_FOR_REVIEW,
        disposition=Disposition(disposition=disp, reasoning="r", confidence=0.8,
                                claim_refs=["c1"], target=Target(name="Dana", email="dana@acme.com")),
        staged_draft=Draft(to="dana@acme.com", subject="PostHog at Acme", body="first touch",
                           angle="x"),
        staged_draft_ref="https://draft/1",
    )


def _rep_msg(date=SENT_DATE):
    return GmailMessage("m1", "t1", "PostHog at Acme", "dana@acme.com", "first touch",
                        date, from_addr="chris.m@posthog.com")


def _loop(ledger, gmail, cadence=CADENCE) -> SlowLoop:
    return SlowLoop(
        ledger=ledger, gmail=gmail, slack=RecordedSlackClient(), model=FakeModel({}),
        rep_config=REP, proposals_dir=Path("/tmp/unused-proposals"),
        voice_profile_path=REPO / "config" / "voice" / "chris.md",
        rubric_path=REPO / "config" / "rubric.md",
        followup_cadence=cadence,
    )


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "l.db")
    yield led
    led.close()


def _detect(ledger, gmail, run, cadence=CADENCE) -> LeadRun:
    """Run only the follow-up state pass and return the persisted run."""
    ledger.insert(run)
    result = SlowLoopResult()
    _loop(ledger, gmail, cadence)._collect_followup_state([run], result)
    return ledger.get(run.id), result


def test_no_send_means_nothing_due(ledger):
    gmail = RecordedGmailClient(sent={})  # nothing sent yet
    run, _ = _detect(ledger, gmail, _run())
    assert run.next_touch_due is None
    assert run.touches == []


def test_send_no_reply_schedules_next_touch(ledger):
    gmail = RecordedGmailClient(sent={"dana@acme.com": [_rep_msg()]},
                                threads={"t1": [_rep_msg()]})
    run, result = _detect(ledger, gmail, _run())
    # first touch recorded with its send time; thread learned
    assert run.thread_id == "t1"
    assert len(run.touches) == 1 and run.touches[0].sent_at == "2026-06-01T10:00:00+00:00"
    assert run.outcome.replied is False
    # due = send + cadence[0] (4 days)
    assert run.next_touch_due == "2026-06-05T10:00:00+00:00"
    assert result.followups_scheduled == 1


def test_target_reply_suppresses_followup(ledger):
    reply = GmailMessage("m2", "t1", "Re: PostHog at Acme", "chris.m@posthog.com",
                         "thanks, interested", "Tue, 02 Jun 2026 09:00:00 +0000",
                         from_addr="dana@acme.com")
    gmail = RecordedGmailClient(sent={"dana@acme.com": [_rep_msg()]},
                                threads={"t1": [_rep_msg(), reply]})
    run, result = _detect(ledger, gmail, _run())
    assert run.outcome.replied is True
    assert run.next_touch_due is None
    assert result.replies_detected == 1


def test_auto_reply_is_not_a_reply(ledger):
    ooo = GmailMessage("m2", "t1", "Automatic reply: Out of office", "chris.m@posthog.com",
                       "I am on vacation until next week.", "Tue, 02 Jun 2026 09:00:00 +0000",
                       from_addr="dana@acme.com")
    gmail = RecordedGmailClient(sent={"dana@acme.com": [_rep_msg()]},
                                threads={"t1": [_rep_msg(), ooo]})
    run, _ = _detect(ledger, gmail, _run())
    assert run.outcome.replied is False
    assert run.next_touch_due == "2026-06-05T10:00:00+00:00"


def test_unreadable_thread_fails_closed(ledger):
    # the send matched but the thread could not be read -> do not schedule
    gmail = RecordedGmailClient(sent={"dana@acme.com": [_rep_msg()]}, threads={})
    run, _ = _detect(ledger, gmail, _run())
    assert run.next_touch_due is None


def test_non_call_disposition_is_skipped(ledger):
    gmail = RecordedGmailClient(sent={"dana@acme.com": [_rep_msg()]},
                                threads={"t1": [_rep_msg()]})
    run, _ = _detect(ledger, gmail, _run(disp=DispositionKind.NURTURE))
    assert run.next_touch_due is None
    assert run.touches == []


def test_single_touch_play_never_schedules(ledger):
    # a lead type with no cadence is single-touch
    gmail = RecordedGmailClient(sent={"dana@acme.com": [_rep_msg()]},
                                threads={"t1": [_rep_msg()]})
    run, _ = _detect(ledger, gmail, _run(), cadence={})
    assert run.outcome.replied is False  # still observed
    assert run.next_touch_due is None


def test_unparseable_send_date_fails_closed(ledger):
    bad = GmailMessage("m1", "t1", "PostHog at Acme", "dana@acme.com", "first touch",
                       "not a date", from_addr="chris.m@posthog.com")
    gmail = RecordedGmailClient(sent={"dana@acme.com": [bad]}, threads={"t1": [bad]})
    run, _ = _detect(ledger, gmail, _run())
    assert run.next_touch_due is None
