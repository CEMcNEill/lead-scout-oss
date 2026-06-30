# Outbound qualifier

## When this handles a lead
A rep/tool-initiated lead from an external sequence (lemlist, slack outbound). It
is excluded at the poll by default; a rep opts in with SF_INCLUDE_OUTBOUND. A lead
still in a live sequence is hard-stopped before it reaches here, so what lands is a
named prospect whose automated sequence is done or paused and who is worth a human
touch.

## How it works
Prospect flow. Read the CRM record, enrich the named person and their company, fold
in PostHog usage only if the contact already maps to an account, and map the use
case from persona plus company. The named person is the target; there is usually
little or no usage of their own to reason from.

## How to judge
A cold-ish prospect the rep already chose to sequence. Weight company fit and the
use case the persona implies; current PostHog usage is usually thin or absent, so do
not penalize its absence. The sequence is done or paused (live ones are
hard-stopped), so a human touch will not collide with automation.

## How to draft
This is a first human touch after an automated sequence, not a cold open. Keep it
short and specific to what their company and role suggest they are trying to do.
Order:
1. Lead with the relevant PostHog use case for their situation.
2. Reference one concrete, grounded detail about them or their company.
3. Close soft with an offer to chat. No hard calendar push, no rehashing of the
   sequence they already saw.

Plain and low-pressure. The fact-check gate still governs every claim, and nothing
is ever sent: this stages a draft for review.

## How to follow up
Two light nudges (3 days, then 6) on the same thread, drafts only, each shorter than
the last. Follow-up 1: a one-liner adding a single fresh, grounded hook (a use case
their stack suggests, a relevant PostHog feature) and an easy out. Follow-up 2: a
brief "happy to close this out if the timing is off" check-in. Do not rehash the
automated sequence or guilt-trip about silence; a reply stops the sequence.

## Notes
The named person is the unit here, unlike the account-first plays. Phase 3 will make
this qualifier agentic; today it runs the deterministic prospect flow.
