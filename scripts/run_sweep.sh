#!/usr/bin/env bash
# Run one lead-agent fast-loop sweep as a local Claude agent, using your connected
# MCPs (Clay, Gmail, Slack) plus the engine. Intended to be called by launchd on a
# schedule. Runs on your machine so your MCP connections are present.
#
# After the sweep it stamps ledger/heartbeat.json (liveness + this run's activity)
# and fires a macOS notification: always on failure, and on success only when the
# sweep actually staged or processed something. `engine.agent_runtime status` reads
# the same heartbeat. Empty sweeps stay silent so the banner means something.
#
# Non-interactive runs need the tools the skill uses to be permitted (Bash for
# `uv run`, and the Clay/Gmail/Slack MCP tools). See SETUP.md for the allowlist.
set -uo pipefail
cd "$(dirname "$0")/.."

START="$(date -u +%Y-%m-%dT%H:%M:%S).000000+00:00"

# --permission-mode acceptEdits so the unattended agent can write the Clay JSON
# under .agent-tmp/ without a prompt; Bash and the Clay/Gmail/Slack MCP tools are
# still gated by the allowlist in .claude/settings.json.
claude -p --permission-mode acceptEdits \
  "Run the lead-agent skill: do one fast-loop sweep now, then report a one-line summary."
CODE=$?

# Stamp the heartbeat and get the notification body (empty = nothing to report).
MSG="$(uv run python -m engine.agent_runtime heartbeat --start "$START" --exit "$CODE" 2>/dev/null || true)"

if command -v osascript >/dev/null 2>&1; then
  if [ "$CODE" -ne 0 ]; then
    osascript -e "display notification \"${MSG:-Sweep failed; see ledger/agent.err.log}\" with title \"lead-scout\" subtitle \"sweep failed\" sound name \"Basso\"" >/dev/null 2>&1 || true
  elif [ -n "$MSG" ]; then
    osascript -e "display notification \"$MSG\" with title \"lead-scout\" subtitle \"sweep complete\"" >/dev/null 2>&1 || true
  fi
fi

exit "$CODE"
