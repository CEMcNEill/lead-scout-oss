"""Slack DM thread tests (recorded client, no live calls)."""

import json
from pathlib import Path

from engine.slack import RecordedSlackClient, SlackNotifier, build_card, build_reasoning
from shared.contracts import (
    Claim,
    Disposition,
    DispositionKind,
    LeadRun,
    RepConfig,
    Route,
    RunStatus,
    Target,
    TriggerSource,
)

REP = RepConfig(
    rep_id="rep_chris", sf_user_id="x", sf_credential_ref="cli",
    gmail_account="chris.m@posthog.com", voice_profile_ref="v", signature="Chris",
    slack_post_target="U08M4JE1U3T", budget_cap_usd=50.0,
)


def _run(**over) -> LeadRun:
    run = LeadRun(
        id="run_1", task_id="t1", rep_id="rep_chris", trigger_source=TriggerSource.BATCH,
        ts="2026-06-28T12:00:00Z", route=Route(lead_type="inbound", qualifier="inbound"),
        status=RunStatus.STAGED_FOR_REVIEW,
        dossier=[
            Claim(id="c1", field="contact_name", value="Sam Rivera", source="crm_context",
                  raw={"lead.name": "Sam Rivera"}, confidence=1.0),
            Claim(id="c2", field="company_name", value="Acme", source="crm_context",
                  raw={"lead.company": "Acme"}, confidence=1.0),
        ],
        disposition=Disposition(disposition=DispositionKind.CALL, reasoning="Clear fit (c1).",
                                confidence=0.82, claim_refs=["c1"],
                                target=Target(name="Sam Rivera", email="sam@acme.com")),
        staged_draft_ref="https://mail.google.com/mail/u/0/#drafts?compose=abc",
    )
    for k, v in over.items():
        setattr(run, k, v)
    return run


def test_card_is_compact_with_draft_link():
    card = build_card(_run())
    assert "Sam Rivera, Acme" in card
    assert "Route: inbound" in card
    assert "**call** (0.82)" in card
    assert "[review & send]" in card and "compose=abc" in card


def test_reasoning_goes_in_thread():
    reasoning = build_reasoning(_run())
    assert "Clear fit" in reasoning
    assert "c1" in reasoning


def test_notifier_posts_card_then_threaded_reasoning():
    client = RecordedSlackClient()
    ts = SlackNotifier(client).notify(_run(), REP)
    assert len(client.posts) == 2
    parent, reply = client.posts
    assert parent["channel"] == "U08M4JE1U3T"
    assert parent["thread_ts"] is None
    assert reply["thread_ts"] == ts  # reasoning is a reply to the card
    assert ts is not None


def test_no_draft_lead_still_posts():
    run = _run(staged_draft=None, staged_draft_ref=None,
               disposition=Disposition(disposition=DispositionKind.NURTURE,
                                       reasoning="Not ready.", confidence=0.5, claim_refs=[]))
    client = RecordedSlackClient()
    SlackNotifier(client).notify(run, REP)
    card = client.posts[0]["text"]
    assert "**nurture**" in card
    assert "Draft:" not in card  # no draft link when there is no draft


def test_blocked_lead_card_and_thread():
    run = _run(status=RunStatus.BLOCKED, hard_stops=["competitor"], disposition=None,
               staged_draft_ref=None, dossier=[])
    client = RecordedSlackClient()
    SlackNotifier(client).notify(run, REP)
    assert "**blocked** (competitor)" in client.posts[0]["text"]
    assert "Hard-stopped" in client.posts[1]["text"]


def test_no_slack_target_is_noop():
    rep = RepConfig(rep_id="r", sf_user_id="x", sf_credential_ref="c", gmail_account="g",
                    voice_profile_ref="v", signature="s", slack_post_target="", budget_cap_usd=1)
    client = RecordedSlackClient()
    assert SlackNotifier(client).notify(_run(), rep) is None
    assert client.posts == []


def test_read_thread_for_judgment_signal():
    client = RecordedSlackClient(thread_messages={
        "171000000.000001": [
            {"user": "U08M4JE1U3T", "text": "card"},
            {"user": "U08M4JE1U3T", "text": "disagree, this is self-serve not a call"},
        ]
    })
    msgs = client.read_thread("U08M4JE1U3T", "171000000.000001")
    assert len(msgs) == 2 and "self-serve" in msgs[1]["text"]


def test_factcheck_flags_surfaced_in_thread():
    run = _run(factcheck_flags=["you raised a Series C"])
    assert "stripped ungrounded" in build_reasoning(run)


def _resolution_claim() -> Claim:
    return Claim(
        id="cR", field="usage_account_resolution",
        value={"chosen": "001B", "ambiguous": True, "candidates": [
            {"id": "001A", "name": "Acme", "org_id": "old", "events_30d": 0},
            {"id": "001B", "name": "Acme", "org_id": "new", "events_30d": 154568},
        ]},
        source="usage_research", raw={"resolution": {}}, confidence=1.0)


def test_card_flags_ambiguous_account():
    run = _run()
    run.dossier.append(_resolution_claim())
    card = build_card(run)
    assert "2 possible PostHog accounts" in card
    assert "confirm in thread" in card


def test_reasoning_lists_candidates_with_links_and_ask():
    run = _run()
    run.dossier.append(_resolution_claim())
    text = build_reasoning(run, sf_account_base="https://posthog.my.salesforce.com")
    assert "Multiple possible PostHog accounts" in text
    assert "reply with the correct one" in text
    assert "[Acme](https://posthog.my.salesforce.com/001B)" in text
    assert "<- used" in text  # marks the engine's pick
    assert "154568 events" in text


def test_no_resolution_block_when_single_candidate():
    run = _run()
    run.dossier.append(Claim(id="cR", field="usage_account_resolution",
                             value={"chosen": "001B", "candidates": [{"id": "001B", "name": "x"}]},
                             source="usage_research", raw={"resolution": {}}, confidence=1.0))
    assert "possible PostHog accounts" not in build_card(run)
