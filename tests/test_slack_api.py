"""Headless Slack bot client tests (fake HTTP, no live calls)."""

import pytest

from engine.slack_api import SlackBotClient, SlackError, to_mrkdwn


class FakeHttp:
    def __init__(self, responses=None):
        self.calls = []
        self._responses = responses or {}

    def __call__(self, method, payload):
        self.calls.append((method, payload))
        if method in self._responses:
            return self._responses[method]
        if method == "chat.postMessage":
            return {"ok": True, "ts": "171.0001", "channel": payload["channel"]}
        if method == "conversations.replies":
            return {"ok": True, "messages": [{"text": "card"}, {"text": "reasoning"}]}
        return {"ok": False, "error": "unknown_method"}


def test_to_mrkdwn_converts_bold_and_links():
    md = "**New lead** — see [review & send](https://mail.google.com/x)"
    assert to_mrkdwn(md) == "*New lead* — see <https://mail.google.com/x|review & send>"


def test_post_message_sends_converted_text():
    http = FakeHttp()
    client = SlackBotClient("xoxb-test", http=http)
    out = client.post_message("U123", "**call** [link](https://x.com/y)")
    assert out["ts"] == "171.0001"
    method, payload = http.calls[0]
    assert method == "chat.postMessage"
    assert payload["channel"] == "U123"
    assert payload["text"] == "*call* <https://x.com/y|link>"
    assert "thread_ts" not in payload


def test_post_reply_sets_thread_ts():
    http = FakeHttp()
    SlackBotClient("t", http=http).post_message("U123", "reply", thread_ts="171.0001")
    assert http.calls[0][1]["thread_ts"] == "171.0001"


def test_read_thread_returns_messages():
    http = FakeHttp()
    msgs = SlackBotClient("t", http=http).read_thread("U123", "171.0001")
    assert [m["text"] for m in msgs] == ["card", "reasoning"]


def test_error_response_raises():
    http = FakeHttp(responses={"chat.postMessage": {"ok": False, "error": "channel_not_found"}})
    with pytest.raises(SlackError):
        SlackBotClient("t", http=http).post_message("Ubad", "hi")


def test_notifier_works_with_bot_client():
    """The bot client drops into SlackNotifier unchanged (it's a SlackClient)."""
    from engine.slack import SlackNotifier
    from shared.contracts import (
        Disposition, DispositionKind, LeadRun, RepConfig, Route, RunStatus, Target,
        TriggerSource,
    )

    rep = RepConfig(rep_id="r", sf_user_id="x", sf_credential_ref="c",
                    gmail_account="g", voice_profile_ref="v", signature="s",
                    slack_post_target="U123", budget_cap_usd=1.0)
    run = LeadRun(id="r1", task_id="t1", rep_id="r", trigger_source=TriggerSource.BATCH,
                  ts="2026-06-29T00:00:00Z", route=Route("inbound", "inbound"),
                  status=RunStatus.STAGED_FOR_REVIEW,
                  disposition=Disposition(DispositionKind.CALL, "why", 0.8, ["c1"],
                                          Target(name="Sam", email="s@x.com")),
                  staged_draft_ref="https://mail.google.com/d")
    http = FakeHttp()
    ts = SlackNotifier(SlackBotClient("t", http=http)).notify(run, rep)
    assert ts == "171.0001"
    assert len(http.calls) == 2  # card + threaded reasoning
    assert "*call*" in http.calls[0][1]["text"]  # converted to mrkdwn
