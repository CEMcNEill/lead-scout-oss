# Running the Lead Outreach Agent

Two ways to run it. The default is the lighter one for your setup.

- Agent + MCP (default): a local Claude agent runs the sweep using the MCPs you
  already have connected (Clay, Gmail, Slack). Salesforce and usage are read by
  the engine directly. Minimal new setup; runs on your Mac on a schedule.
- Headless daemon (alternative): no Claude at run time, but you wire direct API
  clients (Slack bot, Gmail OAuth, an enrichment vendor). More setup; truly
  unattended. See "Headless daemon" at the bottom.

Nothing ever sends to a lead. Drafts are staged in your Gmail for you to send;
the review card is DM'd to you in Slack. Each person installs their own copy under
their own identity; shared config (qualifiers, rubric, routing) updates with
`git pull`, and each machine keeps its own SQLite ledger.

## Agent + MCP setup (default)

1. Clone and install:

       git clone https://github.com/CEMcNEill/lead-scout
       cd lead-scout
       uv sync

2. Env:

       cp .env.example .env

   Set `ANTHROPIC_API_KEY`, `SOURCE=salesforce`, your `SLACK_USER_ID` (the bot/DM
   target for the card), and `SF_INSTANCE_URL`. You do NOT need the Gmail, Slack
   bot, or enrichment keys for this path; those come through the MCPs.

3. Salesforce (act-as-rep):

       sf org login web

4. Connect the MCPs in Claude Code: Clay, Gmail, and Slack (the same connections
   you already use). The skill calls these; the engine handles Salesforce and
   usage itself.

5. Run one sweep on demand, from Claude Code in this repo:

       Run the lead-agent skill

   or headless:

       scripts/run_sweep.sh

6. Schedule it locally:

       scripts/setup.sh

   This installs the skill into `.claude/skills/`, makes `run_sweep.sh`
   executable, and loads a launchd job that sweeps every 5 minutes. It runs on
   your Mac so your MCP connections are present.

   Scheduled (non-interactive) runs need the skill's tools permitted without
   prompts: Bash for `uv run`, and the Clay / Gmail / Slack MCP tools. Grant these
   once via `.claude/settings.json` permissions (an allow list), or run the skill
   interactively first and approve. Without that, the scheduled run will stall on
   a permission prompt.

   Stop it:

       launchctl unload ~/Library/LaunchAgents/com.posthog.lead-agent.agent.plist

What the sweep does: `engine.agent_runtime poll` lists new contact-tasks; for each,
the agent enriches via the Clay MCP, runs `engine.agent_runtime process` (qualify,
fact-check, ledger), creates a Gmail draft via MCP if it's a call, renders the card
with `engine.agent_runtime card`, posts it to your Slack DM, and records the thread.

## Nightly learning

The nightly job (02:30, installed by `scripts/setup.sh`) runs two skills:

- `lead-agent-nightly`: judgment learning (rubric proposals from your disposition
  replies) and confirmed Salesforce account corrections.
- `voice-calibration`: voice learning. It diffs what you sent against what the
  engine staged, measures reply efficacy by recipient segment, and proposes
  voice-profile updates under `config/proposals/`. Both are propose-then-approve;
  run "review my voice profile" to accept changes into `config/voice/chris.md`.

One-time, before relying on the nightly voice scan: run the `voice-calibration`
skill in train mode ("train my voice") to bootstrap the profile from your
historical sent mail on already-worked leads, then "review my voice profile" to
accept the starter rules. Run on demand any time with "run the voice scan".

## Headless daemon (alternative, no Claude at run time)

For a truly unattended service, replace the MCPs with direct clients:

- Slack: a Slack app bot token (`chat:write`, `conversations.replies`) in
  `SLACK_BOT_TOKEN`. The bot DMs you and notifies normally.
- Gmail: a Google Cloud OAuth client (`GMAIL_CLIENT_ID/SECRET`, `GMAIL_ACCOUNT`),
  then `uv run python -m engine.gmail_login` once.
- Enrichment: copy `config/enrichment.example.yaml` to `config/enrichment.yaml`,
  fill your provider's endpoints + field maps, set `ENRICHMENT_API_KEY`.

Then the two daemon jobs run with no Claude session:

    cp deploy/com.posthog.lead-agent.fast.plist ~/Library/LaunchAgents/
    cp deploy/com.posthog.lead-agent.slow.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.posthog.lead-agent.fast.plist
    launchctl load ~/Library/LaunchAgents/com.posthog.lead-agent.slow.plist

Salesforce and usage are identical in both paths. Logs are under `ledger/`.
