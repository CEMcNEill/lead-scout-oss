---
name: lead-scout-setup
description: First-time onboarding for lead-scout. Use when a rep has cloned the repo and wants to get set up without using a separate terminal: installs dependencies, collects config and writes .env, handles the Salesforce login, seeds the voice profile, installs the operational skills, and (with the rep's OK) starts the schedule. Trigger on "set up lead-scout", "onboard me", "run setup", "first-time setup", "get me running".
---

# lead-scout setup (onboarding)

You are onboarding a new rep on their own machine. Work from the repo root. Do the
deterministic work yourself with Bash and the Write tool; ask the rep only for the
values they alone can provide; for anything that needs a browser or a TTY, have the
rep run it with the `!` prefix. Never write a secret into a committed file. Confirm
before starting the recurring schedule. The system never sends to a lead, it only
stages drafts and review cards.

## 0. Preconditions
- Confirm the working directory is the repo root (it has `pyproject.toml` and
  `scripts/setup.sh`).
- Check tooling: `uv --version`, `sf --version`, `python3 --version` (need 3.12+).
  If any is missing, stop and tell the rep how to install it before continuing.
- The default runtime uses the Clay, Gmail, and Slack MCPs. Confirm those are
  connected in Claude Code (you should have `mcp__*Clay*`, `*Gmail*`, `*Slack*`
  tools available). If not, ask the rep to connect them, then continue.

## 1. Dependencies
Run `uv sync --extra dev`.

## 2. Config (.env)
- Get the questions to ask: `uv run python -m engine.configure --fields`. It prints
  JSON: each field has key, label, secret, help; plus the always-forced
  SOURCE/SF_AUTH and the required list.
- Ask the rep for each core field in chat. Secrets (ANTHROPIC_API_KEY and any
  headless tokens) are sensitive: warn that pasting them here puts them in this
  local session, and offer the alternative of entering secrets hidden by running
  `! uv run python -m engine.configure` themselves. Ask whether they want the
  optional headless-daemon integrations (usually no).
- Write the collected `{KEY: value}` map to `.agent-tmp/onboard.config.json` with
  the Write tool (`mkdir -p .agent-tmp` first), then apply it:

      uv run python -m engine.configure --from-json .agent-tmp/onboard.config.json

  Then remove the temp file: `rm -f .agent-tmp/onboard.config.json` (it may hold a
  secret). The command prints any still-missing required values; collect and re-run
  if needed.

## 3. Salesforce login
Check `sf org list`. If it shows a Connected org, skip. Otherwise ask the rep to run
`! sf org login web` themselves (it opens a browser), then confirm with
`sf org list`. Sharing rules scope the engine to the rep's own leads.

## 4. Voice profile + operational skills
- Seed the profile if absent: if `config/voice/chris.md` does not exist, run
  `cp config/voice/chris.example.md config/voice/chris.md`.
- Install the operational skills: `mkdir -p .claude/skills` then
  `cp -R skills/lead-agent skills/lead-agent-nightly skills/voice-calibration .claude/skills/`.

## 5. Schedule (ask first)
Ask with AskUserQuestion: start the 5-minute schedule now, do one manual
verification sweep first, or skip scheduling for now.
- Start now: `scripts/install_jobs.sh`. Loads both jobs; the 5-minute one fires a
  real sweep immediately, staging a Gmail draft and a Slack card for each qualified
  open lead.
- Verify first: `scripts/run_sweep.sh` once, then show the staged drafts
  (`uv run python -m engine.agent_runtime status`, and read the ledger), and offer
  to run `scripts/install_jobs.sh` to start the schedule.
- Skip: leave it; they can run `scripts/install_jobs.sh` whenever.

## 6. Voice training (offer)
Offer to bootstrap voice now: run the voice-calibration skill in train mode ("train
my voice"), then "review my voice profile". Until then drafts use the generic
template voice.

## 7. Report
Summarize what was done: dependencies, .env (note any missing required), Salesforce
connection, voice seeded, skills installed, schedule state, and the next step.
Remind the rep it never sends to a lead, and that `uv run python -m
engine.agent_runtime status` shows whether it ran and what it did.
