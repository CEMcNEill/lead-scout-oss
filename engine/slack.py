"""Slack DM review threads via the connected Slack MCP.

As each lead is processed the fast loop posts to the rep's Slack DM as its own
parent message: a compact card (name, company, route, verdict, link to the Gmail
draft), with the full disposition reasoning as a thread reply so the DM stays
scannable. Leads that produced no draft post too, reasoning in-thread. The parent
message ts becomes slack_thread_ref, and the rep replies in that thread to
disagree with the disposition, which the slow loop reads as the judgment signal.

The Slack boundary sits behind a SlackClient; an MCP-backed client
(mcp__claude_ai_Slack__slack_send_message / slack_read_thread) is injected at the
agent runtime, tests use a recorded client. Posts go only to the rep's own DM
(the review surface), never to a lead.

FUTURE (wanted): the claude.ai Slack MCP posts AS the rep, so the card is a
self-message and Slack suppresses notifications for it -- the rep sees nothing in
their badge. To get real notifications, post FROM a Slack bot (a Slack app with a
chat:write bot token, see SLACK_BOT_TOKEN in .env.example) DMing the rep, or post
to a dedicated channel. A bot-token SlackClient is a drop-in here and also makes
Slack work headlessly (no agent-runtime MCP needed). Kept as-is (self-DM via MCP)
for now by choice.
"""

from __future__ import annotations

from typing import Any, Protocol

from shared.contracts import Claim, LeadRun, RepConfig, RunStatus
from shared.tools.usage import USAGE_RESOLUTION_FIELD


class SlackClient(Protocol):
    def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> dict[str, Any]:
        """Post a message (or a thread reply). Returns at least {"ts": ...}."""
        ...

    def read_thread(self, channel: str, thread_ts: str) -> list[dict[str, Any]]:
        """Parent message plus replies, for the slow loop's judgment signal."""
        ...


def _claim_value(dossier: list[Claim], field: str) -> Any:
    return next((c.value for c in dossier if c.field == field), None)


def account_resolution(run: LeadRun) -> dict[str, Any] | None:
    """The usage account-resolution choices, when there was more than one
    candidate account (for the rep to confirm). None otherwise."""
    value = _claim_value(run.dossier, USAGE_RESOLUTION_FIELD)
    if value and len(value.get("candidates", [])) > 1:
        return value
    return None


def _resolution_block(resolution: dict[str, Any], sf_account_base: str | None) -> str:
    chosen = resolution.get("chosen")
    lines = [
        "Multiple possible PostHog accounts. I went with the one that has usage; "
        "reply with the correct one (name or id) and I'll fix it in Salesforce, no rush:"
    ]
    for c in resolution.get("candidates", []):
        cid, name = c.get("id"), c.get("name")
        label = f"{name}" if sf_account_base is None else f"[{name}]({sf_account_base}/{cid})"
        events = c.get("events_30d")
        mark = "  <- used" if cid == chosen else ""
        lines.append(f"- {label} (`{cid}`) — {events} events/30d{mark}")
    return "\n".join(lines)


def build_card(run: LeadRun) -> str:
    """The compact parent-message card: name, company, route, verdict, draft link."""
    name = (
        _claim_value(run.dossier, "contact_name")
        or (run.disposition.target.name if run.disposition and run.disposition.target else None)
        or run.task_id
    )
    company = _claim_value(run.dossier, "company_name")
    who = f"{name}, {company}" if company else str(name)

    if run.disposition is not None:
        verdict = f"**{run.disposition.disposition.value}** ({run.disposition.confidence:.2f})"
    elif run.hard_stops:
        verdict = f"**blocked** ({', '.join(run.hard_stops)})"
    else:
        verdict = f"**{run.status.value}**"

    lines = [f"**New lead** — {who}", f"Route: {run.route.lead_type}  ·  Verdict: {verdict}"]
    if run.staged_draft_ref:
        lines.append(f"Draft: [review & send]({run.staged_draft_ref})")
    resolution = account_resolution(run)
    if resolution:
        n = len(resolution["candidates"])
        lines.append(f":warning: {n} possible PostHog accounts — confirm in thread")
    return "\n".join(lines)


def build_reasoning(run: LeadRun, sf_account_base: str | None = None) -> str:
    """The thread reply: full reasoning, kept out of the scannable card. Appends
    the account-resolution choices when usage resolution was ambiguous."""
    if run.disposition is not None:
        text = run.disposition.reasoning.strip()
        if run.disposition.claim_refs:
            text += f"\n\n_Claims: {', '.join(run.disposition.claim_refs)}_"
        if run.factcheck_flags:
            text += f"\n\n:warning: stripped ungrounded: {'; '.join(run.factcheck_flags)}"
    elif run.hard_stops:
        text = f"Hard-stopped before research: {', '.join(run.hard_stops)}."
    elif run.error:
        text = f"Run errored: {run.error}"
    else:
        text = "No reasoning recorded."
    resolution = account_resolution(run)
    if resolution:
        text += "\n\n" + _resolution_block(resolution, sf_account_base)
    return text


class SlackNotifier:
    """Posts the review card + reasoning to the rep's DM. Implements Notifier.
    `sf_account_base` (the Salesforce instance URL) hyperlinks candidate accounts
    when usage resolution is ambiguous."""

    def __init__(self, client: SlackClient, *, sf_account_base: str | None = None) -> None:
        self._client = client
        self._sf_account_base = sf_account_base

    def notify(self, run: LeadRun, rep_config: RepConfig) -> str | None:
        channel = rep_config.slack_post_target
        if not channel:
            return None
        parent = self._client.post_message(channel, build_card(run))
        ts = parent.get("ts")
        if ts:
            self._client.post_message(
                channel, build_reasoning(run, self._sf_account_base), thread_ts=ts
            )
        return ts


# --- recorded client (tests / replaying captured MCP output) --------------


class RecordedSlackClient:
    def __init__(self, thread_messages: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.posts: list[dict[str, Any]] = []
        self._threads = thread_messages or {}
        self._counter = 0

    def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> dict[str, Any]:
        self._counter += 1
        ts = thread_ts or f"171000000.{self._counter:06d}"
        self.posts.append({"channel": channel, "text": text, "thread_ts": thread_ts, "ts": ts})
        return {"ts": ts, "link": f"https://slack.com/app_redirect?channel={channel}&ts={ts}"}

    def read_thread(self, channel: str, thread_ts: str) -> list[dict[str, Any]]:
        return list(self._threads.get(thread_ts, []))
