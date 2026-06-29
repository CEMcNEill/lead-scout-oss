#!/usr/bin/env bash
# Per-rep setup helper for the Lead Outreach Agent local service.
# Walks through dependencies, .env, the one-time logins, and the launchd install.
# Idempotent: safe to re-run. See SETUP.md for the full explanation.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
LA="$HOME/Library/LaunchAgents"

echo "==> Installing dependencies (uv sync)"
uv sync

echo "==> Config wizard (writes .env)"
# Interactive: collects the keys the default agent+MCP path needs (and optionally
# the headless-daemon secrets), preserving any values already in .env. Re-runnable
# any time with `uv run python -m engine.configure`.
uv run python -m engine.configure

echo "==> Salesforce login (act-as-rep). A browser will open."
if ! sf org list --json >/dev/null 2>&1 || ! sf org list 2>/dev/null | grep -q Connected; then
  sf org login web
else
  echo "    Salesforce already connected."
fi

echo "==> Gmail consent (headless daemon only; the default agent+MCP runtime uses the Gmail MCP)."
if grep -q '^GMAIL_CLIENT_ID=.\+' .env; then
  uv run python -m engine.gmail_login || echo "    Gmail login skipped/failed; you can run it later."
else
  echo "    GMAIL_CLIENT_ID not set; skipping. Not needed unless you run the no-Claude daemon."
fi

echo "==> Seeding your local voice profile (config/voice/chris.md)"
if [[ ! -f config/voice/chris.md ]]; then
  cp config/voice/chris.example.md config/voice/chris.md
  echo "    Seeded from template. Run \"train my voice\" to learn from your sent mail."
else
  echo "    config/voice/chris.md already present; leaving your trained profile alone."
fi

echo "==> Installing the lead-agent skills into .claude/skills"
mkdir -p "$REPO/.claude/skills"
cp -R "$REPO/skills/lead-agent" "$REPO/skills/lead-agent-nightly" \
      "$REPO/skills/voice-calibration" "$REPO/.claude/skills/"

echo "==> Installing the agent launchd jobs (fast sweep + nightly learning)"
scripts/install_jobs.sh

echo "==> Done."
echo "    Fast sweep every 5 min; nightly learning at 02:30. Logs under ledger/."
echo "    Permissions for non-interactive runs are in .claude/settings.json."
echo "    A no-Claude headless daemon is available via deploy/*.fast/slow.plist."
