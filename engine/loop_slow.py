"""The slow loop: nightly voice and judgment learning.

One nightly job, two sub-loops with identical mechanics, both reading the ledger.
Off-hours, propose-then-approve, versioned. None of the loops invokes another;
this one only reads the ledger plus sent items and Slack threads, and writes
proposals the rep approves.

- Voice sub-loop: for staged-draft runs, match the sent item (the rep edited the
  draft in place and sent it), compute draft_diff, separate substantive edits
  (fact fixes) from stylistic edits (voice) so it learns signal not noise, and
  propose a voice-profile update. Matching is by the draft's actual recipient and
  subject (the target, which may differ from the named lead), scoped to
  staged-draft sends only.
- Judgment sub-loop: for each lead with replies in its Slack thread, parse the
  reply into a human disposition + rationale, compare against the llm
  disposition, and propose rubric updates from the disagreements.

Three rules hold for both: voice fidelity and outreach effectiveness are tracked
as separate objectives; signal is weighted by volume and outcome, not recency;
and it is always propose-then-approve. The system never silently updates its own
voice or rubric: proposals are written as markdown for the rep to approve.
"""

from __future__ import annotations

import datetime as dt
import difflib
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from engine.gmail import GmailClient, GmailMessage
from engine.ledger import Ledger
from engine.salesforce import _soql_escape
from engine.slack import SlackClient, account_resolution
from shared.contracts import Draft, DispositionKind, LeadRun, RepConfig, RunStatus, Touch
from shared.model import ModelClient, ModelTier, parse_json

# Markers that a thread message is an automated bounce/OOO, not a real reply. The
# follow-up loop must not read these as a human reply (which would suppress a due
# follow-up) nor treat them as grounds to stop nudging.
_AUTO_REPLY_MARKERS = (
    "out of office", "automatic reply", "auto-reply", "autoreply",
    "away from my", "on vacation", "vacation responder", "delivery status",
    "mail delivery", "undeliverable",
)
_AUTO_REPLY_SENDERS = ("mailer-daemon", "no-reply", "noreply", "postmaster")


def _parse_email_date(value: str | None) -> dt.datetime | None:
    """Parse an RFC2822 Date header to an aware UTC datetime, or None if it cannot
    be parsed (unknown -> fail closed)."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _addr_matches(from_addr: str | None, account: str | None) -> bool:
    if not from_addr or not account:
        return False
    return account.strip().lower() in from_addr.lower()


def _is_auto_reply(msg: GmailMessage) -> bool:
    blob = f"{msg.subject or ''}\n{msg.body or ''}".lower()
    if any(marker in blob for marker in _AUTO_REPLY_MARKERS):
        return True
    sender = (msg.from_addr or "").lower()
    return any(s in sender for s in _AUTO_REPLY_SENDERS)

# Slack thread shape posted by the notifier: [card, reasoning, ...rep replies].
# Replies beyond the engine's own two messages are the rep's judgment signal.
_ENGINE_THREAD_MESSAGES = 2

# Emoji the engine reacts with to acknowledge it has processed a rep's reply.
_ACK_EMOJI = "white_check_mark"

# Prefix on the threaded ack the MCP path posts back. Because that ack is posted
# as the rep (the MCP has no bot identity), the engine must recognise and skip its
# own acks when reading a thread, or it would treat them as new feedback.
_ACK_MARKER = "✅ lead-scout:"


def _ack_message(human: str | None, llm: str, is_disagreement: bool) -> str:
    """The threaded acknowledgement text. Leads with _ACK_MARKER so the engine can
    filter it out on the next read."""
    base = f"{_ACK_MARKER} Seen and acted on."
    if is_disagreement and human:
        return f"{base} Recorded your call: {human.replace('_', ' ')}."
    if human:
        return f"{base} Confirmed: {llm.replace('_', ' ')}."
    return base


# Salesforce Task fields the rep uses to disposition a lead directly in the CRM,
# read as a judgment signal so learning does not depend on a Slack reply.
_CRM_DISPO_FIELDS = [
    "Id", "Status", "Qualified__c", "Disqualified__c",
    "Disqualification_Reason__c", "Disqualification_Notes__c",
    "Self_Serve_No_Interaction__c",
]


def _crm_disposition(task: dict[str, Any]) -> tuple[str | None, str]:
    """Map a Task's disposition fields to (disposition, rationale). Returns
    (None, "") when the rep has not dispositioned the task in Salesforce.

    Precedence: an explicit disqualify or qualify flag wins over a status value,
    since the rep set it deliberately. Confirmed with the rep 2026-06-30."""
    status = task.get("Status") or ""
    if task.get("Disqualified__c"):
        reason = (task.get("Disqualification_Reason__c") or "").replace(";", ", ").strip()
        notes = (task.get("Disqualification_Notes__c") or "").strip()
        detail = " — ".join(p for p in (reason, notes) if p)
        return "disqualify", (
            f"Rep disqualified in Salesforce: {detail}" if detail
            else "Rep disqualified in Salesforce."
        )
    if task.get("Qualified__c") or status == "Qualified":
        return "call", "Rep marked the task Qualified in Salesforce."
    if task.get("Self_Serve_No_Interaction__c"):
        return "self_serve", "Rep marked the task Self Serve (no interaction) in Salesforce."
    if status == "Nurturing":
        return "nurture", "Rep set the task to Nurturing in Salesforce."
    if status == "In Progress":
        # The rep moved it off Open and is actively working it without disqualifying
        # -- tacit agreement that it is worth a call.
        return "call", "Rep is actively working the task in Salesforce (In Progress)."
    return None, ""


@dataclass
class VoiceEdit:
    task_id: str
    substantive: list[str]
    stylistic: list[str]
    diff: str


@dataclass
class Disagreement:
    task_id: str
    llm_disposition: str
    human_disposition: str
    human_rationale: str


@dataclass
class AccountCorrection:
    task_id: str
    account_id: str | None
    org_id: str | None
    written: bool


@dataclass
class SlowLoopResult:
    voice_edits: list[VoiceEdit] = field(default_factory=list)
    disagreements: list[Disagreement] = field(default_factory=list)
    account_corrections: list[AccountCorrection] = field(default_factory=list)
    runs_with_sent: int = 0
    runs_with_replies: int = 0
    crm_dispositions: int = 0  # rep dispositions read from Salesforce Task fields
    followups_scheduled: int = 0  # runs whose next follow-up was (re)scheduled
    replies_detected: int = 0  # runs where a target reply was detected in-thread
    voice_proposal_path: str | None = None
    rubric_proposal_path: str | None = None
    # Per newly-processed rep reply: a threaded ack the caller posts back. The
    # headless path reacts instead (see _acknowledge_replies); the MCP/agent path
    # has no reactions tool, so it posts these as replies.
    acknowledgements: list[dict[str, str]] = field(default_factory=list)


def _diff(staged: str, sent: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            staged.splitlines(), sent.splitlines(),
            fromfile="staged", tofile="sent", lineterm="",
        )
    )


class SlowLoop:
    def __init__(
        self,
        *,
        ledger: Ledger,
        gmail: GmailClient,
        slack: SlackClient,
        model: ModelClient,
        rep_config: RepConfig,
        proposals_dir: Path,
        voice_profile_path: Path,
        rubric_path: Path,
        sf_writer: Any = None,
        org_id_write_field: str = "posthog_org_id__c",
        followup_cadence: dict[str, list[int]] | None = None,
    ) -> None:
        self._ledger = ledger
        self._gmail = gmail
        self._slack = slack
        self._model = model
        self._rep = rep_config
        self._proposals = Path(proposals_dir)
        self._voice_path = Path(voice_profile_path)
        self._rubric_path = Path(rubric_path)
        self._writer = sf_writer  # something with update_record(sobject, id, fields)
        self._write_field = org_id_write_field
        # lead_type -> follow-up cadence (days). Absent/empty means single-touch, so
        # nothing is ever scheduled (this is what keeps the default behavior intact).
        self._cadence = followup_cadence or {}

    # --- entry point -----------------------------------------------------

    def run(self, stamp: str, *, do_voice: bool = True, do_proposals: bool = True) -> SlowLoopResult:
        """Run the slow loop.

        Judgment recording and account-resolution corrections always run -- they
        are fast, idempotent reactions to a rep reply, fit for the 5-minute sweep.
        Voice diffing (do_voice) and proposal synthesis (do_proposals) are the
        heavier, integrate-over-many-examples parts, left for the nightly run."""
        result = SlowLoopResult()
        runs = self._ledger.list_runs(rep_id=self._rep.rep_id)

        if do_voice:
            result.voice_edits = self._collect_voice_signals(runs, result)
        # Judgment comes from two sources: the rep's Slack thread reply, and the
        # rep dispositioning the Task directly in Salesforce (no reply needed). The
        # CRM is authoritative, so when both fire for a task its disagreement wins.
        slack_disagreements = self._collect_judgment_signals(runs, result)
        crm_disagreements = self._collect_crm_dispositions(runs, result)
        by_task = {d.task_id: d for d in slack_disagreements}
        for d in crm_disagreements:
            by_task[d.task_id] = d
        result.disagreements = list(by_task.values())
        result.account_corrections = self._collect_account_corrections(runs)
        # Follow-up state (sent detection, reply detection, next-touch scheduling)
        # always runs: it is a fast, idempotent read fit for the 5-minute sweep, and
        # the follow-up poll depends on it. It stages nothing -- the followups
        # command (Phase 2c) is the only thing that drafts a follow-up.
        self._collect_followup_state(runs, result)

        if do_proposals:
            if result.voice_edits:
                result.voice_proposal_path = self._propose_voice_update(result.voice_edits, stamp)
            if result.disagreements:
                result.rubric_proposal_path = self._propose_rubric_update(
                    result.disagreements, stamp
                )
        return result

    def run_nightly(self, stamp: str) -> SlowLoopResult:
        return self.run(stamp, do_voice=True, do_proposals=True)

    def run_updates_only(self, stamp: str) -> SlowLoopResult:
        """The light update check for the 5-minute sweep: apply account
        corrections and record overrides; no voice diff, no proposals."""
        return self.run(stamp, do_voice=False, do_proposals=False)

    # --- voice sub-loop --------------------------------------------------

    def _collect_voice_signals(
        self, runs: list[LeadRun], result: SlowLoopResult
    ) -> list[VoiceEdit]:
        edits: list[VoiceEdit] = []
        for run in runs:
            if run.status != RunStatus.STAGED_FOR_REVIEW or run.staged_draft is None:
                continue
            sent = self._match_sent(run.staged_draft)
            if sent is None:
                continue
            result.runs_with_sent += 1
            run.sent_draft = Draft(
                to=run.staged_draft.to, subject=sent.subject, body=sent.body,
                angle=run.staged_draft.angle,
            )
            if sent.body.strip() == run.staged_draft.body.strip():
                run.draft_diff = ""  # sent unchanged: no voice signal, but record it
                self._ledger.update(run)
                continue
            run.draft_diff = _diff(run.staged_draft.body, sent.body)
            self._ledger.update(run)

            classified = self._classify_edits(run.staged_draft.body, sent.body)
            edits.append(
                VoiceEdit(
                    task_id=run.task_id,
                    substantive=classified.get("substantive", []),
                    stylistic=classified.get("stylistic", []),
                    diff=run.draft_diff,
                )
            )
        return edits

    def _match_sent(self, draft: Draft):
        """Match the sent item by the draft's recipient and subject (scoped to
        staged-draft sends). Recipient is the target, which may differ from the
        named lead, so we match on it rather than on the named lead."""
        if not draft.to:
            return None
        query = f'in:sent to:{draft.to} newer_than:60d'
        subject_key = draft.subject.replace("Re:", "").strip().lower()
        for msg in self._gmail.find_sent(query):
            if subject_key and subject_key in (msg.subject or "").lower():
                return msg
        return None

    # --- follow-up state -------------------------------------------------

    def _collect_followup_state(self, runs: list[LeadRun], result: SlowLoopResult) -> None:
        """For each call, learn how many touches the rep has actually sent (from the
        thread), whether the target replied, and when the next touch is due. Stages
        nothing -- that is the followups command's job (Phase 2c). Counting sends from
        the thread (not from staged touches) is what lets the sequence advance past
        touch 1. Fail closed: a touch is scheduled only when the thread was readable,
        at least one send is present, and no reply was seen."""
        for run in runs:
            if run.status != RunStatus.STAGED_FOR_REVIEW or run.staged_draft is None:
                continue
            if run.disposition is None or run.disposition.disposition != DispositionKind.CALL:
                continue  # only call dispositions get a sequence
            thread = self._read_thread(run)
            if thread is None:
                continue  # not sent yet, or thread unreadable -> leave due as is
            rep_msgs = [m for m in thread if _addr_matches(m.from_addr, self._rep.gmail_account)]
            if not rep_msgs:
                continue  # nothing sent yet
            dates = [d for d in (_parse_email_date(m.date) for m in rep_msgs) if d]
            first_sent = min(dates) if dates else None
            last_sent = max(dates) if dates else None
            run.outcome.replied = self._thread_has_reply(thread, run, first_sent)
            if run.outcome.replied:
                result.replies_detected += 1
            self._sync_touch_sends(run, rep_msgs)
            # a follow-up already drafted but not yet sent pauses the cadence: wait
            # for the rep to send it before arming the next one.
            pending = any(t.staged_at and not t.sent_at for t in run.touches)
            run.next_touch_due = (
                None if pending else self._compute_due(run, last_sent, len(rep_msgs))
            )
            if run.next_touch_due:
                result.followups_scheduled += 1
            self._ledger.update(run)

    def _read_thread(self, run: LeadRun) -> list[GmailMessage] | None:
        """The thread's messages, or None when none can be established. Bootstraps
        thread_id from the first matched send the first time through."""
        if not run.thread_id:
            sent = self._match_sent(run.staged_draft)
            if sent is None:
                return None  # not sent yet
            run.thread_id = sent.thread_id or None
            if not run.thread_id:
                return [sent]  # a send with no thread id -> single-message thread
        thread = self._gmail.get_thread(run.thread_id)
        return thread or None

    def _thread_has_reply(self, thread, run: LeadRun, after_dt) -> bool:
        """A reply is any non-rep, non-auto message after our first send. Auto-reply/
        OOO and the rep's own messages do not count."""
        target = (run.staged_draft.to or "").lower()
        replied = False
        for m in thread:
            if _addr_matches(m.from_addr, self._rep.gmail_account):
                continue  # the rep's own message in the thread
            if _is_auto_reply(m):
                continue
            mdt = _parse_email_date(m.date)
            if after_dt is not None and mdt is not None and mdt <= after_dt:
                continue  # predates our first send
            if target and target in (m.from_addr or "").lower():
                return True
            replied = True
        return replied

    def _sync_touch_sends(self, run: LeadRun, rep_msgs: list[GmailMessage]) -> None:
        """Ensure touch 1 exists and stamp sent_at on the touches in send order."""
        if not any(t.n == 1 for t in run.touches):
            run.touches.insert(0, Touch(
                n=1, subject=run.staged_draft.subject, body=run.staged_draft.body,
                staged_at=run.ts, draft_ref=run.staged_draft_ref))
        ordered = sorted(run.touches, key=lambda t: t.n)
        epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        rep_sorted = sorted(rep_msgs, key=lambda m: _parse_email_date(m.date) or epoch)
        for touch, msg in zip(ordered, rep_sorted):
            mdt = _parse_email_date(msg.date)
            if mdt is not None:
                touch.sent_at = mdt.isoformat()

    def _compute_due(self, run: LeadRun, last_sent_dt, sent_count: int) -> str | None:
        """Next-touch time, or None. Scheduled only when we confirmed no reply
        (outcome.replied is False), the last send time is known, and touches remain.
        sent_count is the number of sends already in the thread."""
        if run.outcome.replied is not False:
            return None  # replied, or reply state unknown -> do not schedule
        if last_sent_dt is None:
            return None  # unparseable send time -> fail closed
        cadence = self._cadence.get(run.route.lead_type, [])
        if sent_count < 1 or sent_count > len(cadence):
            return None  # single-touch play, or sequence already complete
        due = last_sent_dt + dt.timedelta(days=cadence[sent_count - 1])
        return due.isoformat()

    def _classify_edits(self, staged: str, sent: str) -> dict[str, Any]:
        resp = self._model.complete(
            system=(
                "You compare a staged outreach draft to the version the rep actually "
                "sent. Separate the edits into 'substantive' (fact changes, fixes, "
                "additions/removals of claims) and 'stylistic' (tone, phrasing, "
                "cadence, word choice that teaches voice). Return JSON "
                '{"substantive": [str], "stylistic": [str]}.'
            ),
            prompt=f"STAGED:\n{staged}\n\nSENT:\n{sent}",
            tier=ModelTier.LEARNING,
            step="slow.classify_edits",
        )
        parsed = parse_json(resp.text)
        return parsed if isinstance(parsed, dict) else {"substantive": [], "stylistic": []}

    def _propose_voice_update(self, edits: list[VoiceEdit], stamp: str) -> str:
        current = self._voice_path.read_text() if self._voice_path.exists() else ""
        stylistic = [s for e in edits for s in e.stylistic]
        resp = self._model.complete(
            system=(
                "You maintain a rep's voice profile (a living rules doc). Given the "
                "current profile and a set of stylistic edits the rep made to staged "
                "drafts before sending, propose concise additions or refinements to "
                "the profile that would make future drafts match the rep's voice. "
                "Weight recurring edits over one-offs. Output the PROPOSED updated "
                "voice rules as markdown. Do not invent rules unsupported by the edits."
            ),
            prompt=(
                f"CURRENT VOICE PROFILE:\n{current}\n\n"
                f"STYLISTIC EDITS ({len(stylistic)} across {len(edits)} sends):\n"
                + "\n".join(f"- {s}" for s in stylistic)
            ),
            tier=ModelTier.LEARNING,
            step="slow.voice_proposal",
        )
        path = self._proposals / stamp / "voice-profile.proposed.md"
        header = (
            f"# PROPOSED voice-profile update ({stamp})\n\n"
            f"Source: {len(stylistic)} stylistic edits across {len(edits)} sent drafts. "
            f"Propose-then-approve: review and apply manually.\n\n---\n\n"
        )
        _write(path, header + resp.text)
        return str(path)

    # --- judgment sub-loop -----------------------------------------------

    def _collect_judgment_signals(
        self, runs: list[LeadRun], result: SlowLoopResult
    ) -> list[Disagreement]:
        disagreements: list[Disagreement] = []
        channel = self._rep.slack_post_target
        if not channel:
            return disagreements
        for run in runs:
            if not run.slack_thread_ref or run.disposition is None:
                continue
            messages = self._slack.read_thread(channel, run.slack_thread_ref)
            # Replies after the engine's two messages, minus our own ack replies.
            # The MCP path posts acks as the rep, so without this filter the engine
            # would read its own ack as fresh feedback and loop.
            replies = [
                m for m in messages[_ENGINE_THREAD_MESSAGES:]
                if not m.get("text", "").startswith(_ACK_MARKER)
            ]
            reply_text = "\n".join(m.get("text", "") for m in replies).strip()
            if not reply_text:
                continue
            # Dedup on the latest rep reply ts: skip a reply we've already
            # acknowledged so the ack fires once, not every sweep. When no ts is
            # present (recorded fixtures) we can't dedup, so fall through.
            latest_ts = next((m["ts"] for m in reversed(replies) if m.get("ts")), None)
            if latest_ts is not None and latest_ts == run.acked_reply_ts:
                continue
            result.runs_with_replies += 1
            parsed = self._parse_reply(reply_text)
            human = parsed.get("disposition")
            run.human_disposition = human
            run.human_rationale = parsed.get("rationale") or reply_text

            llm = run.disposition.disposition.value
            is_disagreement = bool(human and human != llm)
            if is_disagreement:
                disagreements.append(
                    Disagreement(
                        task_id=run.task_id, llm_disposition=llm, human_disposition=human,
                        human_rationale=run.human_rationale,
                    )
                )

            # Acknowledge once, when we can dedup (have a ts): react on the headless
            # path and emit a threaded ack for the MCP path to post.
            if latest_ts is not None:
                self._acknowledge_replies(channel, [m for m in replies if m.get("ts")])
                result.acknowledgements.append({
                    "task_id": run.task_id,
                    "thread_ts": run.slack_thread_ref,
                    "message": _ack_message(human, llm, is_disagreement),
                })
                run.acked_reply_ts = latest_ts
            self._ledger.update(run)
        return disagreements

    def _acknowledge_replies(self, channel: str, replies: list[dict[str, Any]]) -> None:
        """React with a checkmark on each rep reply we just processed, so the rep
        can see at a glance which messages have been picked up. Best-effort: the
        client may not support reactions (the MCP path), the message may lack a ts,
        or Slack may reject the call -- none of that should block the apply. The
        bot client treats already_reacted as success, so this is safe to re-run."""
        react = getattr(self._slack, "add_reaction", None)
        if react is None:
            return
        for message in replies:
            ts = message.get("ts")
            if not ts:
                continue
            try:
                react(channel, ts, _ACK_EMOJI)
            except Exception:  # noqa: BLE001 - acknowledgement must never break the apply
                pass

    # --- CRM disposition signal ------------------------------------------

    def _collect_crm_dispositions(self, runs: list[LeadRun], result: SlowLoopResult):
        """Read the rep's disposition straight off the Salesforce Task, so learning
        does not wait on a Slack reply. Maps the Task's qualify/disqualify/status
        fields to a disposition, records it on the run, and emits a disagreement
        when it differs from the engine's call. Best-effort: a missing query client
        or a failed read is skipped, never fatal. Deduped on the recorded
        disposition so a stable Task is not re-counted every sweep."""
        disagreements: list[Disagreement] = []
        query = getattr(self._writer, "query", None)
        if query is None:
            return disagreements
        by_id = {r.task_id: r for r in runs if r.disposition is not None and r.task_id}
        if not by_id:
            return disagreements
        ids = "', '".join(_soql_escape(t) for t in by_id)
        soql = f"SELECT {', '.join(_CRM_DISPO_FIELDS)} FROM Task WHERE Id IN ('{ids}')"
        try:
            rows = query(soql)
        except Exception:  # noqa: BLE001 - CRM read is best-effort
            return disagreements
        for row in rows or []:
            run = by_id.get(row.get("Id"))
            if run is None:
                continue
            human, rationale = _crm_disposition(row)
            if human is None or run.human_disposition == human:
                continue  # not dispositioned in SF, or already recorded this call
            run.human_disposition = human
            run.human_rationale = rationale
            self._ledger.update(run)
            result.crm_dispositions += 1
            llm = run.disposition.disposition.value
            if human != llm:
                disagreements.append(
                    Disagreement(
                        task_id=run.task_id, llm_disposition=llm,
                        human_disposition=human, human_rationale=rationale,
                    )
                )
        return disagreements

    # --- account-resolution corrections ---------------------------------

    def _collect_account_corrections(self, runs: list[LeadRun]) -> list[AccountCorrection]:
        """When usage resolution was ambiguous and the rep named the correct
        account in the thread, write the corrected PostHog org id back to the Task
        so future runs resolve right. Non-blocking: this happens nightly, after
        the rep replied at their leisure."""
        corrections: list[AccountCorrection] = []
        channel = self._rep.slack_post_target
        if not channel:
            return corrections
        for run in runs:
            if not run.slack_thread_ref:
                continue
            resolution = account_resolution(run)
            if not resolution:
                continue
            messages = self._slack.read_thread(channel, run.slack_thread_ref)
            reply = " ".join(
                m.get("text", "") for m in messages[_ENGINE_THREAD_MESSAGES:]
                if not m.get("text", "").startswith(_ACK_MARKER)
            ).lower()
            if not reply:
                continue
            chosen = self._match_candidate(reply, resolution["candidates"])
            if chosen is None or chosen.get("id") == resolution.get("chosen"):
                continue  # no clear pick, or the rep confirmed the engine's choice
            written = False
            org_id = chosen.get("org_id")
            if self._writer is not None and org_id:
                self._writer.update_record("Task", run.task_id, {self._write_field: org_id})
                written = True
            corrections.append(
                AccountCorrection(task_id=run.task_id, account_id=chosen.get("id"),
                                  org_id=org_id, written=written)
            )
        return corrections

    @staticmethod
    def _match_candidate(reply: str, candidates: list[dict]) -> dict | None:
        matched = [
            c for c in candidates
            if (c.get("id") and str(c["id"]).lower() in reply)
            or (c.get("org_id") and str(c["org_id"]).lower() in reply)
            or (c.get("name") and str(c["name"]).lower() in reply)
        ]
        unique = {c.get("id"): c for c in matched}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def _parse_reply(self, reply_text: str) -> dict[str, Any]:
        resp = self._model.complete(
            system=(
                "A sales rep replied in a lead's review thread to react to the "
                "engine's disposition. Parse their reply into a disposition (one of "
                "call, self_serve, nurture, disqualify) that reflects what they think "
                "the right call is, and a short rationale. If the reply does not "
                "clearly indicate a disposition, return null for disposition. Return "
                'JSON {"disposition": str|null, "rationale": str}.'
            ),
            prompt=reply_text,
            tier=ModelTier.LEARNING,
            step="slow.parse_reply",
        )
        parsed = parse_json(resp.text)
        return parsed if isinstance(parsed, dict) else {"disposition": None, "rationale": ""}

    def _propose_rubric_update(self, disagreements: list[Disagreement], stamp: str) -> str:
        current = self._rubric_path.read_text() if self._rubric_path.exists() else ""
        lines = [
            f"- {d.task_id}: engine said {d.llm_disposition}, rep said "
            f"{d.human_disposition} — {d.human_rationale}"
            for d in disagreements
        ]
        resp = self._model.complete(
            system=(
                "You maintain a shared qualification rubric (markdown). Given the "
                "current rubric and a set of disposition disagreements (where the rep "
                "overruled the engine, with their rationale), propose concise rubric "
                "refinements that would have led the engine to the rep's call. Weight "
                "recurring patterns over one-offs; keep the rubric holistic. Output "
                "the PROPOSED updated rubric as markdown. Do not over-fit to a single "
                "disagreement."
            ),
            prompt=f"CURRENT RUBRIC:\n{current}\n\nDISAGREEMENTS:\n" + "\n".join(lines),
            tier=ModelTier.LEARNING,
            step="slow.rubric_proposal",
        )
        path = self._proposals / stamp / "rubric.proposed.md"
        header = (
            f"# PROPOSED rubric update ({stamp})\n\n"
            f"Source: {len(disagreements)} disposition disagreements. "
            f"Propose-then-approve: review and apply manually.\n\n---\n\n"
        )
        _write(path, header + resp.text)
        return str(path)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
