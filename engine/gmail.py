"""Gmail draft staging via the connected Gmail MCP.

Phase 1.5 turns the drafter's output into a real Gmail draft in the rep's
account, addressed to the disposition's target. Staging as a real draft is what
makes the human loop and voice learning nearly free: the rep edits in place and
sends, and the sent copy is the edited draft, so the slow loop's draft_diff needs
no correlation step.

The Gmail boundary sits behind a GmailClient so the shell and tests never touch
the MCP directly. An MCP-backed client (mcp__claude_ai_Gmail__create_draft /
search_threads / get_thread) is injected at the agent runtime; tests use a
recorded client. Drafts are created, never sent: the guardrail that nothing goes
out autonomously is preserved by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shared.contracts import Draft, RepConfig


@dataclass
class GmailMessage:
    id: str
    thread_id: str
    subject: str
    to: str
    body: str
    date: str
    # the From header. Needed by the follow-up loop to tell a target's reply from
    # the rep's own send when reading a whole thread. Defaults empty for back-compat
    # with older recorded fixtures.
    from_addr: str = ""


class GmailClient(Protocol):
    def create_draft(self, *, to: list[str], subject: str, body: str) -> str:
        """Create a Gmail draft; return its id."""
        ...

    def find_sent(self, query: str) -> list[GmailMessage]:
        """Sent messages matching a Gmail query (for the slow loop's draft_diff)."""
        ...

    def get_thread(self, thread_id: str) -> list[GmailMessage]:
        """All messages in a thread, oldest first (for follow-up reply detection)."""
        ...


def draft_url(draft_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#drafts?compose={draft_id}"


class GmailStagingSink:
    """Stages a Draft as a real Gmail draft. Implements the StagingSink protocol,
    replacing FilesystemStagingSink in Phase 1.5."""

    def __init__(self, client: GmailClient) -> None:
        self._client = client

    def stage(self, run_id: str, rep_config: RepConfig, draft: Draft) -> str:
        to = [draft.to] if draft.to else []
        draft_id = self._client.create_draft(to=to, subject=draft.subject, body=draft.body)
        return draft_url(draft_id)


# --- recorded client (tests / replaying captured MCP output) --------------


class RecordedGmailClient:
    """Records created drafts and replays recorded sent mail. Used in tests and to
    drive a live demo with the MCP results captured separately."""

    def __init__(
        self,
        sent: dict[str, list[GmailMessage]] | None = None,
        threads: dict[str, list[GmailMessage]] | None = None,
    ) -> None:
        self.created: list[dict] = []
        self._sent = sent or {}
        self._threads = threads or {}
        self._counter = 0

    def create_draft(self, *, to: list[str], subject: str, body: str) -> str:
        self._counter += 1
        draft_id = f"r-draft-{self._counter}"
        self.created.append({"id": draft_id, "to": to, "subject": subject, "body": body})
        return draft_id

    def find_sent(self, query: str) -> list[GmailMessage]:
        # match by any recorded key that appears in the query (e.g. a recipient)
        for key, messages in self._sent.items():
            if key in query:
                return list(messages)
        return []

    def get_thread(self, thread_id: str) -> list[GmailMessage]:
        return list(self._threads.get(thread_id, []))
