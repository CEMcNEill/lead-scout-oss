#!/usr/bin/env bash
# Run the nightly lead-agent learning loop as a local Claude agent, using your
# connected MCPs (Gmail, Slack) plus the engine. Called by launchd off-hours.
# Runs on your machine so your MCP connections are present.
set -euo pipefail
cd "$(dirname "$0")/.."

# --permission-mode acceptEdits so the unattended agent can write its working
# files (e.g. .agent-tmp/updates.json, proposal drafts) without a prompt; Bash and
# the MCP tools are still gated by the allowlist in .claude/settings.json.
exec claude -p --permission-mode acceptEdits "Run the lead-agent-nightly skill now (judgment + account corrections), then the voice-calibration skill in scan mode (voice + efficacy). Report a combined summary."
