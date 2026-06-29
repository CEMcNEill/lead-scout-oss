---
name: voice-calibration
description: >
  Learn the rep's writing voice and what actually earns replies, then refine the
  drafting system over time. Reads the sent message and any replies on triaged-lead
  threads, diffs what was sent against what the engine staged (the edits = voice),
  measures efficacy by recipient segment, and proposes updates to a living voice
  profile for human approval. Use when the user says "run the voice scan", "learn
  my voice", "calibrate my voice", "scan what I sent", "review my voice profile",
  "apply voice learnings", "train my voice", "initial voice training", "bootstrap my
  voice profile", or on a nightly schedule. Pairs with the `lead-agent` skill (which
  stages the drafts and writes the ledger this skill reads).
metadata:
  version: "0.2.0"
  author: "Chris McNeill"
---

# Voice Calibration

Close the loop on drafting. Every outbound draft this system stages is reviewed and
edited by the user before it's sent. Those edits, plus which messages earn replies
and from whom, are the training signal. This skill turns that signal into the
living artifacts the drafting flow reads:

- `config/voice/chris.md` -- the approved description of how the rep writes (the
  drafter loads this on every run).
- `config/voice/efficacy-insights.md` -- what's working: reply rate and outcomes by
  recipient segment and message pattern.

It has three modes. train is a one-time bootstrap that seeds the profile from the
rep's historical sent mail on already-worked leads. scan is the recurring off-hours
run (observe + propose). review merges approved proposals into the active profile.
There is a human approval gate: neither train nor scan ever changes `chris.md`
directly -- they write a proposal.

## Project integration (how this maps here)

- The staged-draft ledger is this project's SQLite ledger, not a jsonl file. Read
  the staged drafts (the corpus to diff against) with:
  `uv run python -m engine.agent_runtime voice-corpus` -- one row per staged draft
  with `email`, `subject`, `stagedBody`, `play` (lead type), and segment hints
  (`title`, `company`, `segment`).
- The active profile is `config/voice/chris.md`; do not overwrite it outside review.
- Proposals go to `config/proposals/voice-profile.proposed.md`; scan reports to
  `config/proposals/scans/voice-scan-YYYY-MM-DD.md`; scan state to
  `config/voice/last-scan.json`.
- The engine slow loop handles judgment (disposition overrides) and account
  corrections; this skill owns voice and efficacy. Run the engine's nightly with
  `--no-voice` so it does not also propose voice.
- Compliance floor: the style rules already in `config/voice/chris.md` (plain prose,
  no emdashes, no bold-for-emphasis, drafts only, signed Chris) always win. The
  learned voice tunes within those rules; it never overrides a compliance rule.

## Privacy scope (hard rule)

Only look at threads tied to triaged leads -- threads whose recipient matches a lead
this system worked (from `voice-corpus`, or a Salesforce contact-task owned by the
user with that email). Never scan, read, or learn from the user's personal mail or
any thread not attributable to a known lead. If a thread can't be tied to a lead,
skip it.

## Mode: train (initial bootstrap -- run once, interactively)

Seed the voice profile from the rep's existing sent emails on contact-tasks that are
already worked (Status is not 'Open' -- In Progress, Nurturing, Completed). There
are no staged drafts to diff for these (they predate the system), so train learns
style from the sent messages and efficacy from the replies, then proposes a starter
profile.

T1. Build the historical corpus (privacy scope still applies). Query worked
contact-tasks and follow them to sent mail:

```sql
SELECT Id, Subject, Status, WhoId, ActivityDate, CreatedDate
FROM Task
WHERE OwnerId = '<userId>' AND Who.Type = 'Contact' AND Status != 'Open'
ORDER BY ActivityDate DESC NULLS LAST
LIMIT 300
```

For each task resolve the Contact email and use the Gmail MCP `search_threads`
(`in:sent to:<email>`, then `get_thread`) within a bounded window (default 12
months). Only include threads attributable to one of these leads. De-dup by thread,
cap at ~150 sent messages, note if more exist.

T2. Learn style (no diff available). Extract recurring voice signals along
`references/voice-dimensions.md` directly from how the rep writes, with the observed
frequency as evidence. Only promote a clearly habitual pattern.

T3. Learn efficacy from replies. Classify each thread's outcome per
`references/efficacy-metrics.md`. Aggregate reply/positive rates by segment, always
with sample sizes.

T4. Propose the starter profile (gated). Write
`config/proposals/voice-profile.proposed.md` as a complete first-draft profile, each
rule carrying its evidence. Update `config/voice/efficacy-insights.md`. Write
`config/proposals/scans/voice-train-YYYY-MM-DD.md` and set
`config/voice/last-scan.json`. Do not write `config/voice/chris.md`.

T5. Review. Walk the user through review (below) to accept the starter profile.

## Mode: scan (default, off-hours)

1. Window. Read `config/voice/last-scan.json`; scan threads with activity since
   `lastScanIso` (default last 24h). Record the new value at the end.

2. Gather the triaged-lead threads. Run `voice-corpus` for the staged drafts. For
   each row with a send in the window, locate the Gmail thread (`search_threads` by
   recipient + subject, then `get_thread`). Enforce the privacy scope.

3. Per-thread extraction (see `references/voice-dimensions.md` and
   `references/efficacy-metrics.md`):
   - Sent vs staged diff: compare the sent body to `stagedBody`. Record concrete
     edits along each voice dimension. Where there's no staged body, learn style
     from the sent message alone.
   - Reply signal: did they reply? time-to-reply; a one-line outcome class.
   - Recipient segment: title, role family, company size, play, region (from the
     corpus row, else Salesforce).

4. Aggregate. Voice deltas to candidate rules, promoted only past the recurrence
   threshold in `references/voice-dimensions.md`. Efficacy sliced by segment and
   pattern, with sample sizes; never a "winner" off one or two sends.

5. Write outputs. Update `config/voice/efficacy-insights.md` (observational, no
   gate). Write `config/proposals/voice-profile.proposed.md` (proposed edits, each
   with rationale + evidence + a before/after example). Do not touch
   `config/voice/chris.md`. Write `config/proposals/scans/voice-scan-YYYY-MM-DD.md`.
   Update `config/voice/last-scan.json`.

6. Report. A 3-5 line summary: threads scanned, notable edits, the strongest
   efficacy signal with its sample size, and whether a proposal is waiting. State
   that nothing was applied; running review applies approved changes.

## Mode: review (interactive only -- the approval gate)

Triggered by "review my voice profile" / "apply voice learnings". Show the diff
between `config/voice/chris.md` and `config/proposals/voice-profile.proposed.md` one
change at a time, with its evidence. For each: accept, edit, or reject. Merge
accepted changes into `config/voice/chris.md`, add a dated changelog line at the top,
and clear the consumed proposal. Never apply a change the user didn't accept.

## Guardrails

- Observe only. This skill never sends, never replies, never writes to Salesforce.
  It reads sent mail + replies and writes local learning files.
- Approval gate: voice-profile changes are proposed, never auto-applied.
- Small samples: prefer "not enough data yet" over a confident wrong rule.
- Scope: triaged-lead threads only. No personal mail, ever.
