#!/usr/bin/env bash
# Run the daily follow-up poll as a local Claude agent, using your connected Gmail
# MCP plus the engine. Called by launchd once a day. Runs on your machine so your
# MCP connections are present. Drafts only, never sends to a lead.
set -euo pipefail
cd "$(dirname "$0")/.."

# --permission-mode acceptEdits so the unattended agent can write .agent-tmp work
# files without a prompt; Bash and the MCP tools stay gated by the allowlist in
# .claude/settings.json.
exec claude -p --permission-mode acceptEdits "Run the lead-agent-followups skill now: fetch each staged call's Gmail thread, run the engine follow-up poll, and stage drafts-only follow-ups for the ones now due. Report a summary."
