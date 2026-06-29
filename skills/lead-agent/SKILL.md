---
name: lead-agent
description: Run one lead-agent sweep + update check. Polls the rep's open Salesforce contact-tasks, enriches via the Clay MCP, qualifies and drafts with the engine, stages a Gmail draft, posts a review card to the rep's Slack DM, then applies any thread replies (account corrections + recorded overrides). Drafts only, never sends to a lead. Trigger on "run the lead agent", "lead sweep", "process my leads".
---

# Lead-agent sweep (agent + MCP)

You orchestrate one sweep. The engine does the deterministic and grounded work
(dedup, routing, qualification, the fact-check gate, the ledger, learning); you
provide the MCP input and output for Clay, Gmail, and Slack. Salesforce and usage
are read by the engine itself via the `sf` CLI, so you do not call a Salesforce
MCP here.

Hard rules: never send email (drafts only). Post Slack only to the rep's own DM
(their user id, env `SLACK_USER_ID`). Do not invent data; pass MCP results to the
engine verbatim. If a step errors, skip that lead and continue; report what was
skipped.

Unattended: this runs non-interactively under launchd. Never call AskUserQuestion
or any prompt that needs a human; if a step would need one, skip that lead and
continue. Use the Write tool (not shell redirection like `cat >` or `touch`) for
every file you create, so the run does not stall on a permission prompt.

Run all engine commands from the repo root with `uv run`. Write Clay JSON under
`.agent-tmp/` (gitignored; `mkdir -p .agent-tmp` first), using the Write tool.

## Steps

1. Poll for new leads:

       uv run python -m engine.agent_runtime poll

   This prints a JSON list of `{task_id, company_domain, contact_name,
   contact_email}` for tasks not already processed. If empty, report "no new
   leads" and stop.

2. For each target, in order:

   a. Enrich the company via the Clay MCP `find-and-enrich-company` with
      `companyIdentifier = company_domain`. Save the full JSON response to
      `.agent-tmp/<task_id>.company.json`.

   b. Enrich the contact via the Clay MCP `find-and-enrich-list-of-contacts` with
      `[{contactName: contact_name, companyIdentifier: company_domain}]`. Save the
      full JSON to `.agent-tmp/<task_id>.contact.json`. (A not-found contact is fine.)

   c. Qualify:

          uv run python -m engine.agent_runtime process --task <task_id> \
            --clay-company .agent-tmp/<task_id>.company.json \
            --clay-contact .agent-tmp/<task_id>.contact.json

      This prints `{status, disposition, draft}`. The run is already written to
      the ledger. If `draft` is null (disposition was not "call", or it was
      blocked), skip to step (f) to still post the review card.

   d. If `draft` is present, create a Gmail draft via the Gmail MCP `create_draft`
      with `to = [draft.to]`, `subject = draft.subject`, `body = draft.body`.
      Take the returned draft id and form the URL
      `https://mail.google.com/mail/u/0/#drafts?compose=<id>`.

   e. (Skip if no draft.)

   f. Render the Slack card, passing the draft URL when there is one:

          uv run python -m engine.agent_runtime card --task <task_id> \
            --draft-url <url-or-omit>

      This prints `{card, reasoning}`.

   g. Post to the rep's DM via the Slack MCP `slack_send_message` with
      `channel_id = SLACK_USER_ID` and `message = card`. From the response take both
      the message `ts` AND the `channel` it resolved to (the DM channel id, starts
      with `D`) -- replies are read back from that channel id, not the user id. Post
      `reasoning` as a reply with `thread_ts = <ts>` on the same `channel`. If env
      `SLACK_DM_CHANNEL_ID` is unset, this resolved `D...` channel is its value:
      tell the rep to set it (`uv run python -m engine.configure`) so idle sweeps
      that post nothing can still read replies.

   h. Record the thread:

          uv run python -m engine.agent_runtime set-thread --task <task_id> --ts <ts>

3. Update check (apply any replies the rep left since the last sweep):

   a. `uv run python -m engine.agent_runtime slow-targets` lists runs with a
      `slack_thread_ref`. For each, use the Slack MCP `slack_read_thread` with
      `channel_id = SLACK_DM_CHANNEL_ID` (the DM channel, starts with `D`; NOT
      `SLACK_USER_ID` -- a user id is rejected for reads) and
      `message_ts = slack_thread_ref`, and collect the messages under
      `threads[<slack_thread_ref>]` as `{text, user}` in `.agent-tmp/updates.json`
      (leave `sent` empty: `{"sent": {}, "threads": {...}}`). If `SLACK_DM_CHANNEL_ID`
      is unset, fall back to the `D...` channel captured from a card post this sweep;
      if none was posted this sweep, skip the read and note that
      `SLACK_DM_CHANNEL_ID` must be set for replies to be detected.

   b. Apply them (corrections + recorded overrides only, no proposals):

          uv run python -m engine.agent_runtime slow-run \
            --data .agent-tmp/updates.json --updates-only

      If the rep named the correct account in a thread, this writes the right
      PostHog org id back to the Task; if they disagreed with the disposition, it
      records the override. The heavier voice/rubric learning is the separate
      nightly skill, not this step.

4. Report a one-line summary: leads processed, staged drafts, blocked, skipped,
   and updates applied.

## Notes

- The card flags when usage account resolution was ambiguous and lists candidate
  accounts; the rep replies in the thread to pick the right one. The nightly slow
  loop reads that reply and corrects Salesforce. You do not act on it here.
- This skill is the fast loop. The nightly learning loop runs separately.
