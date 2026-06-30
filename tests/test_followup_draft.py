"""Phase 2c: follow-up draft pass + the boundary (process_followup) + the chain.

qualifier.follow_up reuses the dossier/disposition and threads a Re: draft; the
shell mirrors the boundary (gate, stage, append a Touch, pause the cadence); and
the slow loop re-arms the next touch only after the prior one is sent, stopping at
max_touches. A reply at any point suppresses. Drafts only; FakeModel, no live calls.
"""

import json
from pathlib import Path

import pytest

from engine.cost import BudgetGovernor, ModelPolicy
from engine.gmail import GmailMessage, RecordedGmailClient
from engine.hardstops import HardStopConfig
from engine.ledger import Ledger
from engine.loop_slow import SlowLoop, SlowLoopResult
from engine.providers import FixedClock, NullStagingSink, StubToolProvider
from engine.router import Router
from engine.shell import Shell
from engine.slack import RecordedSlackClient
from shared.contracts import (
    Claim,
    Disposition,
    DispositionKind,
    Draft,
    LeadRun,
    RepConfig,
    Route,
    RunStatus,
    Target,
    Touch,
    TriggerSource,
)
from shared.model import FakeModel
from shared.registry import build_default_registry
from qualifiers.startup_rolloff.qualifier import StartupRolloffQualifier
from tests.fixtures.conformance_cases import _plg_world

REPO = Path(__file__).resolve().parent.parent
RUBRIC = "Holistic. No single axis disqualifies."
REP = RepConfig(
    rep_id="rep_chris", sf_user_id="x", sf_credential_ref="cli",
    gmail_account="chris.m@posthog.com", voice_profile_ref="v", signature="Chris",
    slack_post_target="", budget_cap_usd=50.0,
)
CADENCE = {"startup_rolloff": [4, 7]}  # max 3 touches


def _dossier():
    return [Claim(id="c1", field="monthly_event_volume", value=6_000_000,
                  source="usage_research", raw={"events_30d": 6_000_000}, confidence=0.99)]


def _run(*, touches, disp=DispositionKind.CALL) -> LeadRun:
    return LeadRun(
        id="run_sr", task_id="t_plg", rep_id="rep_chris",
        trigger_source=TriggerSource.BATCH, ts="2026-05-30T12:00:00+00:00",
        route=Route("startup_rolloff", "startup_rolloff"),
        status=RunStatus.STAGED_FOR_REVIEW, dossier=_dossier(),
        disposition=Disposition(disposition=disp, reasoning="r", confidence=0.8,
                                claim_refs=["c1"], target=Target("Dana", "dana@acme.com")),
        staged_draft=Draft("dana@acme.com", "PostHog at Acme", "first touch",
                           "plg-rolloff-led", ["c1"]),
        staged_draft_ref="https://draft/1", thread_id="t1", touches=touches,
    )


def _model(factcheck_grounded=True) -> FakeModel:
    fc = ([{"assertion": "~6M events/mo", "claim_ref": "c1"}] if factcheck_grounded
          else [{"assertion": "you raised a Series C", "claim_ref": None}])
    return FakeModel({
        "drafter": json.dumps({"subject": "PostHog at Acme",
                               "body": "Quick nudge before your credits expire.",
                               "claims_used": ["c1"]}),
        "factcheck": json.dumps(fc),
    })


def _shell(ledger, model) -> Shell:
    return Shell(
        ledger=ledger,
        router=Router.from_yaml(REPO / "qualifiers" / "registry.yaml"),
        registry=build_default_registry(RUBRIC),
        hard_stops=HardStopConfig.from_yaml(REPO / "config" / "hard_stops.yaml"),
        governor=BudgetGovernor(ModelPolicy(), per_run_cap_usd=5.0, per_day_cap_usd=1000.0),
        inner_model=model,
        tool_provider=StubToolProvider(_plg_world(), voice_profile="Plain.", exemplar_bank={}),
        staging_sink=NullStagingSink(), clock=FixedClock(),
    )


def _slowloop(ledger, gmail) -> SlowLoop:
    return SlowLoop(
        ledger=ledger, gmail=gmail, slack=RecordedSlackClient(), model=FakeModel({}),
        rep_config=REP, proposals_dir=Path("/tmp/unused"),
        voice_profile_path=REPO / "config" / "voice" / "chris.md",
        rubric_path=REPO / "config" / "rubric.md", followup_cadence=CADENCE,
    )


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "l.db")
    yield led
    led.close()


def _touch1_sent():
    return Touch(n=1, subject="PostHog at Acme", body="first touch",
                 staged_at="2026-05-30T12:00:00+00:00", sent_at="2026-06-01T10:00:00+00:00",
                 draft_ref="https://draft/1")


# --- qualifier.follow_up --------------------------------------------------


def test_follow_up_threads_a_re_draft_reusing_dossier():
    model = _model()
    tb = StubToolProvider(_plg_world(), voice_profile="Plain.", exemplar_bank={}).fetchers(REP)
    from shared.tools.toolbox import build_toolbox

    toolbox = build_toolbox(
        crm_fetcher=tb[0], person_fetcher=tb[1], company_fetcher=tb[2], usage_fetcher=tb[3],
        model=model, voice_profile="Plain.", exemplars=[],
    )
    run = _run(touches=[_touch1_sent()])
    draft = StartupRolloffQualifier(RUBRIC).follow_up(run, 2, toolbox)
    assert draft is not None
    assert draft.subject == "Re: PostHog at Acme"  # threaded onto touch 1
    assert draft.to == "dana@acme.com"


def test_follow_up_returns_none_for_non_call():
    run = _run(touches=[_touch1_sent()], disp=DispositionKind.NURTURE)
    assert StartupRolloffQualifier(RUBRIC).follow_up(run, 2, tools=None) is None


# --- shell.process_followup ----------------------------------------------


def test_process_followup_stages_re_touch_and_pauses(ledger):
    run = _run(touches=[_touch1_sent()])
    ledger.insert(run)
    updated = _shell(ledger, _model()).process_followup(run, REP)
    assert updated.staged_draft.subject == "Re: PostHog at Acme"
    assert len(updated.touches) == 2 and updated.touches[1].n == 2
    assert updated.touches[1].staged_at is not None and updated.touches[1].sent_at is None
    assert updated.next_touch_due is None  # paused until the rep sends it


def test_process_followup_factcheck_gate_strips_ungrounded(ledger):
    run = _run(touches=[_touch1_sent()])
    ledger.insert(run)
    updated = _shell(ledger, _model(factcheck_grounded=False)).process_followup(run, REP)
    assert len(updated.touches) == 1  # no new touch staged
    assert updated.factcheck_flags  # the ungrounded assertion was recorded


def test_process_followup_stops_at_max_touches(ledger):
    sent = [Touch(n=i, subject="s", body="b", staged_at="x",
                  sent_at=f"2026-06-0{i}T10:00:00+00:00") for i in (1, 2, 3)]
    run = _run(touches=sent)
    ledger.insert(run)
    updated = _shell(ledger, _model()).process_followup(run, REP)
    assert len(updated.touches) == 3  # 3 = max for cadence [4, 7]; nothing added
    assert updated.next_touch_due is None


def test_process_followup_does_not_double_stage_a_pending_touch(ledger):
    pending = Touch(n=2, subject="Re: PostHog at Acme", body="nudge",
                    staged_at="2026-06-05T00:00:00+00:00", sent_at=None)
    run = _run(touches=[_touch1_sent(), pending])
    ledger.insert(run)
    updated = _shell(ledger, _model()).process_followup(run, REP)
    assert len(updated.touches) == 2  # the pending touch was not re-staged


# --- the full chain: advance on send, complete at the end ----------------


def _rep_msg(mid, date):
    return GmailMessage(mid, "t1", "PostHog at Acme", "dana@acme.com", "touch", date,
                        from_addr="chris.m@posthog.com")


def test_chain_advances_on_each_send_and_completes(ledger):
    model = _model()
    shell = _shell(ledger, model)
    ledger.insert(_run(touches=[]))

    # touch 1 sent -> due at +4 days
    g = RecordedGmailClient(sent={"dana@acme.com": [_rep_msg("m1", "Mon, 01 Jun 2026 10:00:00 +0000")]},
                            threads={"t1": [_rep_msg("m1", "Mon, 01 Jun 2026 10:00:00 +0000")]})
    _slowloop(ledger, g)._collect_followup_state([ledger.get("run_sr")], SlowLoopResult())
    assert ledger.get("run_sr").next_touch_due == "2026-06-05T10:00:00+00:00"

    # stage touch 2 -> cadence pauses until it is sent
    shell.process_followup(ledger.get("run_sr"), REP)
    assert ledger.get("run_sr").next_touch_due is None
    assert len(ledger.get("run_sr").touches) == 2

    # rep sends touch 2 -> due re-arms at +7 days from the latest send
    msgs2 = [_rep_msg("m1", "Mon, 01 Jun 2026 10:00:00 +0000"),
             _rep_msg("m2", "Fri, 05 Jun 2026 10:00:00 +0000")]
    g = RecordedGmailClient(threads={"t1": msgs2})
    _slowloop(ledger, g)._collect_followup_state([ledger.get("run_sr")], SlowLoopResult())
    assert ledger.get("run_sr").next_touch_due == "2026-06-12T10:00:00+00:00"

    # stage touch 3 (the last), then the rep sends it -> sequence complete
    shell.process_followup(ledger.get("run_sr"), REP)
    msgs3 = msgs2 + [_rep_msg("m3", "Fri, 12 Jun 2026 10:00:00 +0000")]
    g = RecordedGmailClient(threads={"t1": msgs3})
    _slowloop(ledger, g)._collect_followup_state([ledger.get("run_sr")], SlowLoopResult())
    assert ledger.get("run_sr").next_touch_due is None  # no 4th touch


def test_chain_reply_suppresses_followup(ledger):
    ledger.insert(_run(touches=[_touch1_sent()]))
    reply = GmailMessage("r1", "t1", "Re: PostHog at Acme", "chris.m@posthog.com",
                         "interested!", "Tue, 02 Jun 2026 09:00:00 +0000", from_addr="dana@acme.com")
    sent = _rep_msg("m1", "Mon, 01 Jun 2026 10:00:00 +0000")
    g = RecordedGmailClient(threads={"t1": [sent, reply]})
    _slowloop(ledger, g)._collect_followup_state([ledger.get("run_sr")], SlowLoopResult())
    r = ledger.get("run_sr")
    assert r.outcome.replied is True
    assert r.next_touch_due is None
