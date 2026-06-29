"""Exemplar-bank bootstrap tests: status filtering, recipient matching, labeling,
dedup, and round-trip to disk."""

import json

from engine.bootstrap import (
    ContactedTask,
    SentMessage,
    StubContactedTaskSource,
    StubSentMailReader,
    build_exemplar_bank,
    write_bank,
)
from shared.contracts import RepConfig

REP = RepConfig(
    rep_id="rep_chris", sf_user_id="005x", sf_credential_ref="kc",
    gmail_account="chris.m@posthog.com", voice_profile_ref="v", signature="Chris",
    slack_post_target="U1", budget_cap_usd=50.0,
)


def _tasks():
    return [
        ContactedTask("t1", "inbound", "sam@acme.com", "Sam", "in_progress"),
        ContactedTask("t2", "big_fish", "dana@acme.com", "Dana", "nurturing"),
        ContactedTask("t3", "inbound", "lee@delta.com", "Lee", "closed_won"),  # filtered out
        ContactedTask("t4", "inbound", "noreply@x.com", "X", "in_progress"),   # no sent mail
    ]


def _mail():
    return {
        "sam@acme.com": [
            SentMessage("sam@acme.com", "Funnels at Acme", "Saw your funnels break...", "2026-05-01"),
            SentMessage("sam@acme.com", "Funnels at Acme", "Saw your funnels break...", "2026-05-09"),  # dup
        ],
        "dana@acme.com": [
            SentMessage("dana@acme.com", "PostHog at Acme", "Your usage is spiking...", "2026-05-03"),
        ],
        "lee@delta.com": [
            SentMessage("lee@delta.com", "Hi Lee", "should be excluded by status", "2026-04-01"),
        ],
    }


def test_build_bank_filters_status_and_matches_recipient():
    bank = build_exemplar_bank(
        StubContactedTaskSource(_tasks()), StubSentMailReader(_mail()), REP
    )
    # inbound has Sam (dedup'd to 1), big_fish has Dana
    assert set(bank) == {"inbound", "big_fish"}
    assert len(bank["inbound"]) == 1  # duplicate send collapsed
    assert bank["inbound"][0].startswith("Subject: Funnels at Acme")
    assert "spiking" in bank["big_fish"][0]
    # closed_won task excluded; task with no sent mail contributes nothing
    assert "lee@delta.com" not in json.dumps(bank)


def test_max_per_type_cap():
    tasks = [
        ContactedTask("a", "inbound", "a@x.com", "A", "in_progress"),
        ContactedTask("b", "inbound", "b@x.com", "B", "in_progress"),
    ]
    mail = {
        "a@x.com": [SentMessage("a@x.com", "one", "first", "d")],
        "b@x.com": [SentMessage("b@x.com", "two", "second", "d")],
    }
    bank = build_exemplar_bank(
        StubContactedTaskSource(tasks), StubSentMailReader(mail), REP, max_per_type=1
    )
    assert len(bank["inbound"]) == 1


def test_write_and_reload(tmp_path):
    bank = build_exemplar_bank(
        StubContactedTaskSource(_tasks()), StubSentMailReader(_mail()), REP
    )
    path = tmp_path / "exemplars.json"
    write_bank(bank, path)
    assert json.loads(path.read_text()) == bank
