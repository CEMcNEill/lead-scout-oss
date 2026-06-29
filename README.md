# lead-scout

A local assistant that watches your Salesforce leads, researches and qualifies
each one, drafts outreach in your voice, and stages it for your review. It never
sends anything to a lead. Every run ends at a Gmail draft you choose to send (or
delete) and a review card in your Slack DM.

A "lead" here is an Open Salesforce Task tied to a Contact (this build does not use
Lead objects). For each Open lead the system reads the CRM record, enriches the
person and company, pulls the account's PostHog usage and Vitally billing, makes
one holistic qualify-or-not judgment, and, only when it's worth a human, drafts a
use-case-led message to the right contact.

## What it does, step by step

On each sweep, for every new Open lead:

1. Read the Salesforce Task plus its Contact and Account.
2. Skip it on a hard-stop (do-not-contact, competitor, personal email, or a
   teammate's account).
3. Route it by the Task subject tag (for example `[Product-led]` to the PLG
   qualifier, `[Default Contact Form]` to inbound).
4. Research it: enrich the person and company (Clay), and resolve the account's
   PostHog usage and billing from the fields synced onto the Salesforce Account.
5. Judge it holistically against the rubric: call, self-serve, nurture, or
   disqualify. No single signal decides it.
6. If the judgment is "call", draft the email in your voice and check every factual
   claim against the grounded research before staging.
7. Stage the draft in your Gmail and post a review card to your Slack DM. Leads
   that didn't warrant a draft post too, with the reasoning.

Then a nightly pass learns from what you actually did: it diffs the messages you
sent against what it staged (your edits teach voice), reads your Slack replies
(where you overrule a call), and proposes updates to your voice profile and the
rubric for you to approve. It never changes them on its own.

## How it runs

Two options. The default is lighter for this setup.

- Agent plus MCP (default): a local Claude agent runs the work using the Clay,
  Gmail, and Slack connections you already have in Claude Code. Salesforce and
  usage are read directly. This is what the instructions below set up.
- Headless daemon (alternative): no Claude at run time, but you wire direct API
  clients (a Slack bot token, a Gmail OAuth app, an enrichment vendor) and run
  plain launchd jobs. See SETUP.md.

Each person installs their own copy under their own identity. Shared config
(qualifiers, rubric, routing) updates with `git pull`; each machine keeps its own
SQLite ledger.

## Install

Requires Python 3.12 and uv, the Salesforce CLI (`sf`), and Claude Code.

    git clone https://github.com/CEMcNEill/lead-scout
    cd lead-scout
    uv sync --extra dev

Or skip the terminal: clone the repo, open it in Claude Code, and say "set up
lead-scout". The shipped `lead-scout-setup` skill onboards you conversationally
(dependencies, config, Salesforce login, voice profile, skills, and the schedule).
The terminal path below does the same thing.

## Configure

1. Create your env file. The config wizard prompts for everything and writes
   `.env`:

       uv run python -m engine.configure

   It collects the core fields the default path needs (`ANTHROPIC_API_KEY`, your
   `SLACK_USER_ID` for review cards, `SF_INSTANCE_URL`, your sign-off name and
   booking link) and, if you opt in, the headless-daemon secrets. `scripts/setup.sh`
   runs it for you; re-run it any time to change values. Secrets stay in `.env`
   (gitignored); OAuth refresh tokens live in the macOS Keychain. You can also just
   `cp .env.example .env` and edit by hand.

2. Log in to Salesforce as yourself and make your org the CLI default:

       sf org login web
       sf config set target-org you@yourorg.com --global

   Sharing rules scope the engine to your own leads.

3. Connect the Clay, Gmail, and Slack MCPs in Claude Code (the same ones you
   already use). The skills call these; the engine reads Salesforce and usage
   itself.

4. Optional: person/company enrichment. Copy `config/enrichment.example.yaml` to
   `config/enrichment.yaml`, fill in your provider's endpoints and field maps, and
   set `ENRICHMENT_API_KEY`. Skip it to start; dossiers are just thinner until then.

Only Tasks with `Status = 'Open'` are processed (In Progress and Nurturing are for
a later phase; Completed is ignored).

## Commands

### Everyday use (skills, said in Claude Code in the repo)

- `Run the lead-agent skill` -- one sweep: poll Open leads, enrich, qualify, stage
  Gmail drafts, post Slack cards, and apply any replies you left.
- `Run the lead-agent-nightly skill` -- judgment learning (rubric proposals) and
  confirmed Salesforce account corrections.
- `train my voice` -- one-time: bootstrap your voice profile from historical sent
  mail on already-worked leads.
- `run the voice scan` -- the recurring voice learning (diff sent vs staged,
  efficacy by segment, propose profile updates).
- `review my voice profile` -- the approval gate: accept or reject proposed voice
  rules into `config/voice/[rep name].md`.

### Scheduling (launchd, runs on your Mac)

- `scripts/setup.sh` -- full onboarding: deps, the config wizard, Salesforce login,
  voice seed, installs the skills into `.claude/skills/`, and loads the launchd jobs.
- `scripts/install_jobs.sh` -- just install and load the two launchd jobs (a
  5-minute sweep with the update check and nightly learning at 02:30). Use this to
  start the schedule on its own.
- `scripts/run_sweep.sh` -- run one sweep now (what the 5-minute job calls).
- `scripts/run_nightly.sh` -- run the nightly learning now.
- `launchctl unload ~/Library/LaunchAgents/com.posthog.lead-agent.agent.plist` --
  stop the 5-minute job (use `load` to start it; same for `.nightly`).

Non-interactive runs use the permission allowlist in `.claude/settings.json`. Logs
are under `ledger/`. The first time the 5-minute job loads it fires a real sweep
over all your Open leads, so expect a batch (one draft and one card per qualified
lead).

Each sweep stamps `ledger/heartbeat.json` and posts a macOS notification: always
on failure, and on success only when it actually staged or processed something, so
idle sweeps stay quiet. Run `uv run python -m engine.agent_runtime status` any time
to see when it last ran and what it did.

### Engine CLI

The building blocks the skills call. Run them directly to debug or process one
lead by hand, with `uv run python -m engine.agent_runtime <command>`:

- `status [--json]` -- did it run, and what did it do: last sweep time, success or
  failure, that sweep's activity, and ledger totals. Reads `ledger/heartbeat.json`.
- `poll` -- list new Open leads, each with its enrichment targets (domain, name).
- `process --task <id> [--clay-company <file>] [--clay-contact <file>]` -- qualify
  one lead: read Salesforce + usage, run the qualifier and fact-check gate, write
  the ledger, and print the draft to stage.
- `card --task <id> [--draft-url <url>]` -- render the Slack card and reasoning for
  a processed lead.
- `set-thread --task <id> --ts <ts>` -- record the Slack thread id so the nightly
  loop can read your replies.
- `slow-targets` -- list what the nightly run needs to fetch (staged drafts, thread
  ids).
- `voice-corpus` -- dump staged drafts as the corpus the voice scan diffs against.
- `slow-run --data <file> [--updates-only] [--no-voice]` -- run the slow loop over
  fetched Gmail and Slack data. `--updates-only` is the light 5-minute pass (apply
  replies, no proposals); `--no-voice` leaves voice to the voice-calibration skill.

### Auth and one-time logins

- `uv run python -m engine.configure` -- the config wizard: prompts for every value
  and writes `.env`, preserving anything already set. Run it any time to update.
- `sf org login web` -- log in to Salesforce as yourself.
- `sf config set target-org you@yourorg.com --global` -- set the default org.
- `uv run python -m engine.gmail_login` -- one-time Gmail consent (only for the
  headless-daemon path; the agent path uses the Gmail MCP).

### Headless daemon (alternative to the agent path)

- `MODE=fast SOURCE=salesforce uv run python -m engine.service` -- one fast sweep
  with no Claude session (needs the Slack bot, Gmail OAuth, and enrichment clients
  configured; see SETUP.md).
- `MODE=slow SOURCE=salesforce uv run python -m engine.service` -- one nightly pass.

### Development

- `uv sync --extra dev` -- install dependencies including the test tools.
- `uv run pytest` -- run the test suite (deterministic; no live calls).

## Layout

    engine/        shell, ledger, router, fact-check gate, cost, loops, adapters,
                   Salesforce / Clay / Gmail / Slack / usage clients, agent runtime
    shared/        contracts, model interface, the toolbox primitives, conformance
    qualifiers/    the four qualifiers (SKILL.md + logic) and the routing registry
    skills/        lead-agent, lead-agent-nightly, voice-calibration skills
    .claude/skills/ lead-scout-setup, the shipped onboarding skill
    config/        rubric, voice profile, hard-stops, enrichment template
    deploy/        launchd plists
    scripts/       setup, install_jobs, and run wrappers
    tests/         fixture-based tests

## Guardrails

It never sends email or Slack messages to a lead, and never makes a CRM write, on
its own. Drafts and suggestions only, for your review. The single Salesforce write
it makes is a PostHog account correction you explicitly confirmed in a thread.
Every factual claim in a draft traces to grounded research; the fact-check gate
drops anything that does not.

See SPEC.md for the authoritative design, SETUP.md for per-rep onboarding and the
headless-daemon path, and CLAUDE.md for build conventions.
