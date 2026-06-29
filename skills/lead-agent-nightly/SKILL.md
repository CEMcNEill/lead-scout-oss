---
name: lead-agent-nightly
description: Run the nightly lead-agent learning loop. Reads your sent edits (Gmail MCP) and Slack thread replies, proposes versioned voice/rubric updates (never auto-applied), and writes back confirmed account corrections to Salesforce. Trigger on "run nightly learning", "lead agent nightly", "learn from my edits".
---

# Lead-agent nightly learning (agent + MCP)

You gather the human-loop signals via MCP and hand them to the engine, which does
the diffing, classification, proposal synthesis, and Salesforce write-backs. It
is propose-then-approve: voice and rubric changes are written as markdown under
`config/proposals/` for the rep to review, never applied automatically. The only
Salesforce write is a confirmed account correction the rep asked for in a thread.

Run engine commands from the repo root with `uv run`. Write the data file under
`.agent-tmp/` (`mkdir -p .agent-tmp` first).

## Steps

1. List what to fetch:

       uv run python -m engine.agent_runtime slow-targets

   This prints a JSON list of `{task_id, draft_to?, draft_subject?,
   slack_thread_ref?}`. If empty, report "nothing to learn from yet" and stop.

2. Build a data file `.agent-tmp/slow.json` with two maps:

   - `sent`: for each target that has `draft_to`, use the Gmail MCP
     (`search_threads` with `query: "in:sent to:<draft_to>"`, then `get_thread`
     for the body) to find the message the rep actually sent. Add it under
     `sent[<draft_to>]` as a list of `{to, subject, body, date, id, thread_id}`.
     Skip targets where you find no matching sent message.

   - `threads`: for each target that has `slack_thread_ref`, use the Slack MCP
     `slack_read_thread` (`channel_id = SLACK_USER_ID`, `message_ts =
     slack_thread_ref`) and add the returned messages under
     `threads[<slack_thread_ref>]` as a list of `{text, user}`.

   Shape:

       {"sent": {"dana@acme.com": [{"subject": "...", "body": "...", ...}]},
        "threads": {"171000000.000001": [{"text": "..."}, ...]}}

3. Run the learning pass:

       uv run python -m engine.agent_runtime slow-run --data .agent-tmp/slow.json

          uv run python -m engine.agent_runtime slow-run \
            --data .agent-tmp/slow.json --no-voice

   It prints how many disagreements and account corrections it found, and the path
   of any rubric proposal. `--no-voice` leaves voice learning to the
   voice-calibration skill (run that separately, see step 5).

4. Report the rubric/correction summary, and point the rep at any proposal file to
   review. Do not apply proposals; that is the rep's call.

5. Run the voice scan: invoke the `voice-calibration` skill in scan mode. It diffs
   your sent edits against the staged drafts and proposes voice-profile updates
   (also gated). Report its summary too.

## Notes

- Voice signal comes from the diff between the staged draft and the sent message,
  split into substantive vs stylistic edits.
- Judgment signal comes from the rep's thread reply, compared to the engine's
  disposition; disagreements drive a proposed rubric refinement.
- An account correction is written to Salesforce only when the rep named a
  different account than the engine used in the thread.
