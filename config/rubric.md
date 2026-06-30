# Qualification rubric (shared, team-level)

This rubric is holistic. Evidence is gathered across every dimension and weighed
as a whole. No single axis disqualifies. A firmographically weak lead with
explosive usage is a valid qualify, because the judgment is made against the
complete dossier, not a sequence of pass/fail checks.

## Dispositions

- call: worth a personal, human touch now. There is a plausible use case, a
  reachable person who plausibly owns or influences the decision, and enough
  signal that outreach is a good use of the rep's time.
- self_serve: real fit, but the right next step is product-led self-onboarding,
  not a rep email. Strong product motion, low present need for a human.
- nurture: not ready now, but worth revisiting. Keep warm, no outreach yet.
- disqualify: not a fit, or not actionable. Use sparingly and only on evidence.

## How to weigh

- Usage trajectory and live use cases are strong positive signals, especially
  for product-led leads where the account, not the named person, is the unit.
- Usage must be live and attributable to a current person. Usage signal is only
  load-bearing when it reflects real, ongoing activity by someone still at the
  company. Discount usage heavily when:
  - the power users who generated it no longer work there (verify the named
    person and key users are current employees), or
  - the usage is flat or steady at a low level over a long period. Long-stable
    low usage indicates a settled self-serve account with no expansion trigger,
    not an active buying motion.
- Expansion signal, not just presence of usage, is what justifies a call. A
  clear upward trajectory, a new use case, or a crossed threshold is the
  positive signal. Absent any movement, low or static usage points to self_serve
  or nurture, not call.
- Persona matters: someone who owns or influences the budget raises the value of
  a call; an end user with no buying influence lowers it (but may still be a
  champion). A champion only counts if they are a current, active user.
- Firmographic fit is one input among several, never a gate on its own.
- Respect the Sales Assist threshold. Accounts below the team's Sales Assist
  threshold are generally not worth a personal touch now unless paired with a
  genuine expansion trigger (sharp usage growth, new team onboarding, a buying
  persona newly engaged). Below-threshold plus static or declining usage points
  to self_serve or nurture; reserve call for a concrete, recent trigger.
- Prefer call when a clear use case maps to a PostHog product and a buyable or
  champion persona is present and there is live, current activity or a recent
  expansion trigger, even if other axes are mixed.

## Competitor and conflict-of-interest screen

- Before applying the holistic weighing, check whether the lead's company is a
  direct competitor of PostHog (e.g., a rival product analytics, session
  replay, feature flag, or developer-analytics platform). A competitor is
  generally not a genuine sales prospect, regardless of how strong the usage,
  persona, or firmographic signals appear.
- When the dossier indicates a competitor, prefer disqualify. Strong usage by a
  competitor often reflects evaluation or intelligence-gathering, not buying
  intent, and should not be read as a positive sales signal.
- This is the one place where a single axis can be decisive. It is a
  prospect-validity screen, not a fit score: a competitor can look like an ideal
  customer on every other axis and still be unactionable as a sales lead.
- Apply judgment, not keyword matching. Adjacent tooling, partial overlap, or a
  company that merely uses similar terminology is not necessarily a competitor;
  reserve this screen for genuine head-to-head products. When the competitive
  relationship is ambiguous, treat it as one input among several rather than a
  gate, and consider nurture over disqualify.

## Disqualify vs. nurture

- disqualify still requires evidence and should be used sparingly. The cleanest
  disqualify cases are: usage driven by people who have left, or persistently
  below-threshold accounts with no movement. Where the account is a real but
  small self-serve user with no current trigger, prefer self_serve or nurture
  over call, but defer to the rep's threshold judgment; a "send a message,
  reopen if they engage" pass is a legitimate disqualify.

## Output discipline

Reference the specific Claims (by id) that drive the decision. The reasoning must
be auditable: a reviewer should be able to follow each load-bearing statement
back to a grounded Claim. Calibrate confidence honestly; a mixed dossier should
not produce false certainty. When invoking the competitor screen, cite the Claim
that establishes the competitive relationship explicitly. When usage is a
load-bearing positive, cite the Claim establishing it is recent or trending and
attributable to a current employee; if either is missing or unverifiable, do not
let usage alone carry a call disposition.
