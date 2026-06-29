# CLAUDE.md — Lead Outreach Agent

## What we're building
A headless, always-on service that monitors my Salesforce leads and newly
assigned tasks, researches and qualifies each, examines PostHog usage, maps the
use case, and stages outreach drafts for my review. It NEVER sends on its own —
every output stops at a staged draft or suggestion that I approve.

## Architecture
This build is QUALIFIER-ORCHESTRATED: a thin shell owns the boundary (hard-stops in, fact-check gate + ledger + cost out); agentic per-type qualifiers own the interior using shared tools, and every qualifier must pass the conformance suite before it is registered. See SPEC.md.

## Source of truth
SPEC.md is the complete and authoritative design. Build strictly against it. If
something is ambiguous or missing, ask me before inventing it. Do not import
patterns or assumptions from outside SPEC.md.

## How to build
- Follow SPEC.md's "Build phasing" section in order. Phase 1 first. Don't jump ahead.
- Start by planning: read SPEC.md fully and propose a phased plan. Do NOT write
  code until I approve the plan.
- Stub every external integration (Salesforce, Gmail, Slack, Clay, PostHog)
  behind a clean interface first, with fixtures, so the engine runs end-to-end
  on fake data before any real credentials are wired.
- Write tests as you go (pytest). The fact-check invariant, the ledger, and
  task dedup especially need deterministic tests.

## Conventions
- Python 3.12, managed with uv. Type hints throughout. Standard-library SQLite
  for the ledger.
- Readable and inspectable over clever. No premature abstraction.
- Secrets come from environment / macOS Keychain, never hardcoded. See .env.example.

## Hard guardrails
- Never send email or Slack messages, and never make irreversible CRM writes,
  autonomously. Staged drafts and suggestions only, for human review.
- Every factual claim in a draft must trace to a grounded source per SPEC.md's
  fact-check invariant. No ungrounded assertion reaches a draft.

## My style (for any docs or generated copy)
Plain prose, no emdashes, no bold-for-emphasis, markdown not docx.
