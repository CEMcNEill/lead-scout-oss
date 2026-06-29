#!/usr/bin/env bash
# Install and load the two launchd jobs (5-minute sweep + 02:30 nightly), with the
# repo path substituted into each plist. Shared by scripts/setup.sh and the
# lead-scout-setup skill. Idempotent: re-running reloads them.
#
# Loading the agent job fires one sweep immediately (RunAtLoad), so it starts
# staging Gmail drafts and Slack cards right away. To pause later:
#   launchctl unload ~/Library/LaunchAgents/com.posthog.lead-agent.agent.plist
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
LA="$HOME/Library/LaunchAgents"

chmod +x "$REPO/scripts/run_sweep.sh" "$REPO/scripts/run_nightly.sh"
mkdir -p "$LA" "$REPO/ledger"
for job in agent nightly; do
  src="deploy/com.posthog.lead-agent.$job.plist"
  dst="$LA/com.posthog.lead-agent.$job.plist"
  sed "s#/Users/evilmac/Dev/lead-scout#$REPO#g" "$src" > "$dst"
  launchctl unload "$dst" 2>/dev/null || true
  launchctl load "$dst"
  echo "    loaded $job"
done
