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

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.gmail import GmailClient
from engine.ledger import Ledger
from engine.slack import SlackClient, account_resolution
from shared.contracts import Draft, LeadRun, RepConfig, RunStatus
from shared.model import ModelClient, ModelTier, parse_json

# Slack thread shape posted by the notifier: [card, reasoning, ...rep replies].
# Replies beyond the engine's own two messages are the rep's judgment signal.
_ENGINE_THREAD_MESSAGES = 2


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
    voice_proposal_path: str | None = None
    rubric_proposal_path: str | None = None


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
        result.disagreements = self._collect_judgment_signals(runs, result)
        result.account_corrections = self._collect_account_corrections(runs)

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
            replies = messages[_ENGINE_THREAD_MESSAGES:]
            reply_text = "\n".join(m.get("text", "") for m in replies).strip()
            if not reply_text:
                continue
            result.runs_with_replies += 1
            parsed = self._parse_reply(reply_text)
            human = parsed.get("disposition")
            run.human_disposition = human
            run.human_rationale = parsed.get("rationale") or reply_text
            self._ledger.update(run)

            llm = run.disposition.disposition.value
            if human and human != llm:
                disagreements.append(
                    Disagreement(
                        task_id=run.task_id, llm_disposition=llm, human_disposition=human,
                        human_rationale=run.human_rationale,
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
            reply = " ".join(m.get("text", "") for m in messages[_ENGINE_THREAD_MESSAGES:]).lower()
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
