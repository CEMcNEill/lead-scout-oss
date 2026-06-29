"""Exemplar-bank cold-start bootstrap.

Voice learns best from real sends, and the corpus already exists. This job scans
the rep's Salesforce lead tasks that they have already reached out to (status
in-progress or nurturing), finds the matching messages in the rep's Gmail sent
items, labels each by lead type, and writes them to the exemplar bank the drafter
retrieves from.

Both inputs are behind narrow interfaces with Phase 1 stubs: the real
ContactedTaskSource is a Salesforce SOQL query (wired with SF auth) and the real
SentMailReader is a Gmail read (the read half of the Gmail integration, separate
from draft staging). The matching here is recipient-based, which is the right
cold-start signal; the ongoing slow loop matches by thread instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from shared.contracts import RepConfig

_ELIGIBLE_STATUSES = {"in_progress", "nurturing"}


@dataclass
class ContactedTask:
    task_id: str
    lead_type: str
    contact_email: str
    contact_name: str
    status: str


@dataclass
class SentMessage:
    to: str
    subject: str
    body: str
    date: str


class ContactedTaskSource(Protocol):
    def list_contacted(self, rep_config: RepConfig) -> list[ContactedTask]:
        """Lead tasks the rep has already reached out to."""
        ...


class SentMailReader(Protocol):
    def sent_to(self, email: str) -> list[SentMessage]:
        """The rep's sent messages addressed to this recipient."""
        ...


# --- Phase 1 stubs --------------------------------------------------------


class StubContactedTaskSource:
    def __init__(self, tasks: list[ContactedTask]) -> None:
        self._tasks = list(tasks)

    def list_contacted(self, rep_config: RepConfig) -> list[ContactedTask]:
        return list(self._tasks)


class StubSentMailReader:
    def __init__(self, by_recipient: dict[str, list[SentMessage]]) -> None:
        self._by_recipient = by_recipient

    def sent_to(self, email: str) -> list[SentMessage]:
        return list(self._by_recipient.get(email, []))


# --- the job --------------------------------------------------------------


def format_exemplar(message: SentMessage) -> str:
    return f"Subject: {message.subject}\n\n{message.body.strip()}"


def build_exemplar_bank(
    task_source: ContactedTaskSource,
    mail_reader: SentMailReader,
    rep_config: RepConfig,
    *,
    max_per_type: int | None = None,
) -> dict[str, list[str]]:
    """Assemble {lead_type: [exemplar, ...]} from contacted tasks and sent mail."""
    bank: dict[str, list[str]] = {}
    for task in task_source.list_contacted(rep_config):
        if task.status not in _ELIGIBLE_STATUSES:
            continue
        messages = mail_reader.sent_to(task.contact_email)
        if not messages:
            continue
        bucket = bank.setdefault(task.lead_type, [])
        for message in messages:
            if max_per_type is not None and len(bucket) >= max_per_type:
                break
            exemplar = format_exemplar(message)
            if exemplar not in bucket:  # avoid duplicate sends polluting the bank
                bucket.append(exemplar)
    return bank


def write_bank(bank: dict[str, list[str]], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bank, indent=2))
