"""Headless Slack client over the Slack Web API (bot token).

The agent-runtime Slack client posts via the claude.ai MCP, which only exists in
a session and posts as the rep (so Slack suppresses the notification). For a
standalone service the review card is posted by a Slack app bot token instead: it
runs headless and, because the bot is not the rep, Slack notifies normally.

Implements the SlackClient seam, so the notifier and slow loop are unchanged.
Card/reasoning text is authored in standard markdown; this client converts it to
Slack mrkdwn (*bold*, <url|text>) before posting. Posts go to the rep's DM (their
user id as the channel) or a configured channel; never to a lead.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

_API = "https://slack.com/api/"


class SlackError(RuntimeError):
    pass


def to_mrkdwn(text: str) -> str:
    """Convert the standard markdown the card/reasoning builders emit into Slack
    mrkdwn: **bold** -> *bold*, [label](url) -> <url|label>."""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", text)
    return text


# transport: (api_method, payload) -> parsed json. Injectable for tests.
SlackHttp = Callable[[str, dict[str, Any]], dict[str, Any]]


def _urllib_http(token: str) -> SlackHttp:
    def call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(_API + method, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed Slack endpoint
            return json.loads(resp.read().decode())

    return call


class SlackBotClient:
    """SlackClient backed by a bot token. Headless, notifies the rep."""

    def __init__(self, token: str, *, http: SlackHttp | None = None) -> None:
        self._http = http or _urllib_http(token)

    def post_message(
        self, channel: str, text: str, thread_ts: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"channel": channel, "text": to_mrkdwn(text)}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        resp = self._http("chat.postMessage", payload)
        if not resp.get("ok"):
            raise SlackError(f"chat.postMessage failed: {resp.get('error')}")
        return {"ts": resp.get("ts"), "channel": resp.get("channel")}

    def read_thread(self, channel: str, thread_ts: str) -> list[dict[str, Any]]:
        resp = self._http("conversations.replies", {"channel": channel, "ts": thread_ts})
        if not resp.get("ok"):
            raise SlackError(f"conversations.replies failed: {resp.get('error')}")
        return list(resp.get("messages", []))


def build_slack_client_from_env() -> SlackBotClient:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is not set; required for the headless Slack client")
    return SlackBotClient(token)
