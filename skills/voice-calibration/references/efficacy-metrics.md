# Efficacy metrics

What "worked" means, how to slice it, and how to avoid fooling ourselves on small
samples.

## Outcome ladder (per thread)

Classify each scanned thread's latest state:

1. `no_reply_yet` — sent, inside the wait window, no response
2. `no_reply_stale` — past the wait window, still nothing
3. `auto_or_ooo` — bounce / out-of-office / autoresponder (exclude from rates)
4. `reply_question` — they replied with a question (engaged)
5. `reply_objection` — replied but pushed back (price, timing, not now)
6. `reply_positive` — interested / wants to continue
7. `meeting_booked` — call scheduled (the north-star outcome)

**Reply rate** = (4+5+6+7) / (all non-`auto_or_ooo` sends).
**Positive rate** = (6+7) / same denominator.

## Segments to slice by

Pull from the ledger row or the Salesforce Contact/Account:

- **Title / seniority** — IC / Manager / Director / VP / C-level / Founder
- **Role family** — Eng, Product, Data, Growth/Marketing, Founder/exec, Ops
- **Company size** — by employee band (1-10, 11-50, 51-200, 201-500, 500-1000, 1000+)
- **Play** — inbound / onboarding / transition / big-fish / board-direct
- **Message pattern** — CTA type (question vs calendar link), length band, whether
  it opened with a concrete metric, whether it named a competitor/integration
- **Send timing** — hour-of-day, day-of-week (does morning send beat evening?)

## Confidence discipline

- Always report **n** alongside any rate. A "60% reply rate" on n=2 is noise.
- Don't declare a winner for a slice with **n < 5**. Mark it `low-n, watching`.
- Prefer directional language tied to evidence: "VP+ at 500+ employees: 4/5 replied
  to a question CTA vs 1/6 to a calendar link (n small, watching)."
- Track cumulatively across scans so slices mature; cite cumulative n.
- Watch for confounds: a high reply rate on "transition" leads may be the billing
  urgency, not the wording. Note plausible confounds rather than over-attributing.

## What efficacy-insights.md should contain

A compact, decision-useful summary, refreshed each scan:

1. **Headline** — the 1–3 strongest, sufficiently-powered signals.
2. **By segment** — small tables of reply/positive rate with n, per slice that has
   enough data.
3. **Patterns that earn replies** — openings/CTAs/lengths that over-index, with n.
4. **Patterns that don't** — what to stop doing, with n.
5. **Still learning** — slices with too little data, so the next scan knows to watch.

Keep it short enough that the drafting flow can load and apply it quickly.
