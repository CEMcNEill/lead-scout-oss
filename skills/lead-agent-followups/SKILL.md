---
name: lead-agent-followups
description: Run the daily follow-up poll. Reads the Gmail thread of each staged call (Gmail MCP), lets the engine detect replies and decide which sequences are due, then stages drafts-only follow-ups (Re: on the same thread) for the rep to review. Never sends. Trigger on "run follow-ups", "lead agent followups", "stage my follow-ups".
---

# Lead-agent follow-ups (agent + MCP)

You gather each lead's Gmail thread via MCP and hand it to the engine, which
detects whether the target replied, decides which sequences are due, and drafts
the next touch. It is drafts-only: the engine never sends, and a detected reply
suppresses the follow-up (fail closed). The rep reviews and sends each draft.

Run engine commands from the repo root with `uv run`. Write the data file under
`.agent-tmp/` (`mkdir -p .agent-tmp` first).

## Steps

1. List what to fetch:

       uv run python -m engine.agent_runtime followup-targets

   Prints a JSON list of `{task_id, draft_to, draft_subject, thread_id}` for every
   staged call. If empty, report "no sequences to follow up on yet" and stop.

2. Build `.agent-tmp/followups.json` with a `gmail_threads` map. For each target,
   locate its Gmail thread: if `thread_id` is set, use the Gmail MCP `get_thread`;
   otherwise `search_threads` with `query: "to:<draft_to>"` and match the subject,
   then `get_thread`. Add the full thread under `gmail_threads[<thread_id>]` as a
   list of `{id, thread_id, from, to, subject, body, date}` (include the `from`
   header on every message: the engine uses it to tell the rep's sends from a
   target reply). Skip targets whose thread you cannot find.

   Shape:

       {"gmail_threads": {"<thread_id>": [
          {"id": "...", "thread_id": "...", "from": "chris.m@posthog.com",
           "to": "dana@acme.com", "subject": "PostHog at Acme",
           "body": "...", "date": "Mon, 01 Jun 2026 10:00:00 +0000"}]}}

3. Run the poll:

       uv run python -m engine.agent_runtime followups --data .agent-tmp/followups.json

   It refreshes reply/due state from the threads, then for every sequence now due
   (and not replied to) drafts the next touch. It prints
   `{due, staged, runs: [{task_id, draft: {to, subject, body}}|...]}`. A run with
   `draft: null` was due but withheld (fact-check or sequence complete).

4. For each run with a non-null `draft`, create the Gmail draft via the Gmail MCP
   `create_draft`, addressed to `draft.to`, threaded onto the existing thread
   (same subject, which is already `Re: ...`). Drafts only -- never send. This is
   the same drafts-only staging the first-touch sweep does.

5. Report: how many sequences were due, how many follow-up drafts you staged, and
   any that were withheld. Remind the rep nothing was sent; the drafts await their
   review.

## Notes

- Cadence and max touches are per play (the engine owns them); you do not decide
  timing. You only fetch threads and stage what the engine returns.
- A reply from the target (not an auto-reply/OOO) stops the sequence; the engine
  handles that, so a thread with a real reply will produce no follow-up.
- Never send, never write to Salesforce here. This skill stages Gmail drafts only.
