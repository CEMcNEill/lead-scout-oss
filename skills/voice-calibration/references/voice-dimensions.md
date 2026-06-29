# Voice dimensions

The axes to compare when diffing a **sent** message against the **staged** draft.
Each recurring edit along an axis is a candidate voice rule. Capture the concrete
before/after, not a vague label.

| Dimension | What to look for in the edit | Example signal |
|-----------|------------------------------|----------------|
| Greeting | "Hi {first}," vs "Hey {first}," vs no greeting | User consistently changes "Hi" → "Hey" |
| Opening line | Does the user cut the system's first sentence and start with the point? | Removes warm-up, leads with the hook |
| Length | Word-count delta sent vs staged; sentences added/removed | Trims ~30% every time → "shorter than staged" |
| Sentence shape | Long/compound → short/punchy, or vice versa | Splits long sentences |
| Hedging | Adds or removes "just", "maybe", "I think", "happy to" | Removes hedges → more direct |
| Specificity | Swaps generic phrasing for a concrete number/fact | Adds a metric or a named integration |
| Formatting | Bullets → prose, removes bold, link placement | Always converts a list to prose |
| CTA style | "grab a time here" vs "reply with a couple of times" vs question | Prefers a question over a calendar link |
| Sign-off | "Best, Chris" vs "Cheers, Chris" vs "— Chris" | Consistent sign-off variant |
| Subject line | How the user rewrites the subject | Shortens; drops "Re:" |
| Vocabulary | Recurring word swaps (e.g. "utilize" → "use") | Maintain a swap list |

## Recurrence threshold (promote vs ignore)

- A pattern becomes a **proposed rule** only when it appears in **≥3 sends** and in
  **≥60%** of opportunities where it could apply, in the current scan window or
  carried over from prior scans.
- Below that, hold it as a "watching" note in the scan report — don't propose it.
- A single dramatic edit is an anecdote. Voice is the repeated move.

## Carry-over

Patterns accumulate across scans. Keep a small running tally per pattern in the
scan reports so a rule that's at 2 sends tonight can cross the threshold next
scan. The proposal should cite the cumulative evidence, not just tonight's.

## Writing a proposed rule

Each proposed rule in `voice-profile.proposed.md` should be one line the drafting
flow can act on, plus evidence:

> **Greeting:** open with "Hey {first}," not "Hi {first},". _(7/8 sends; e.g. APL
> Logistics draft "Hi Barış" → sent "Hey Barış")_

Keep rules concrete and behavioral. Avoid personality adjectives ("be punchy") in
favor of actions ("cut the first warm-up sentence; lead with the hook").
