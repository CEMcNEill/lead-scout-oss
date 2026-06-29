# Lead Outreach Agent — Build Spec (qualifier-orchestrated)

## What this is

An agent that takes a Salesforce lead task assigned to a rep, builds a grounded, fully-researched picture of the person, company, and (where relevant) PostHog usage, makes a holistic qualification judgment, and — only if warranted — drafts outreach in the rep's voice for review.

It is a **headless service running on localhost**: a scheduled process that polls Salesforce, drafts into Gmail, keeps a ledger, and learns off-hours. There is no screen to build; the human touchpoints borrow surfaces the rep already lives in (Gmail, Slack, markdown files).

The system pairs a thin deterministic shell with **agentic, per-lead-type qualifiers**. Each lead type is handled by its own qualifier — an agentic Claude worker that researches, judges, and drafts for that type, using a shared toolbox of research and drafting primitives. The shell handles the edges (triggering, hard-stops, the final grounding gate, the ledger, cost), and trusts each qualifier with the interior reasoning.

Two principles sit underneath everything:

1. **Research-then-judge.** Evidence is gathered across every dimension and weighed as a whole. No single axis disqualifies. A firmographically weak lead with explosive usage is a valid qualify, because the qualifier judges against the complete dossier, not a sequence of pass/fail checks.
2. **No fact without a source.** Grounding is a property of the data itself, enforced when facts are created and again when they are used. Fact-checking is an invariant of the system, not a stage in it.

---

## The mental model: three loops over a shared ledger

The system is **three loops running on three different clocks, coupled only through a shared ledger**.

- **The fast loop — machine clock, every 5 minutes.** Poll Salesforce, find lead tasks not already in the ledger, route each to its qualifier, stage a draft. Perception-to-action: it closes the gap between "a lead arrived" and "a draft is waiting." A uniform 5-minute poll makes inbound feel near-real-time and sweeps up product-led leads in the same pass. At realistic volume this may mean a Salesforce webhook is never needed.
- **The human loop — the rep's clock, asynchronous.** The rep reviews, edits, sends (or doesn't), and disagrees with dispositions. Runs on attention, not a timer. This is the loop that injects ground truth.
- **The slow loop — calendar clock, nightly.** Read what the fast loop staged against what the human loop actually did: diff voice from sent items, collect disposition overrides, propose versioned updates to the voice profile and the rubric, hold them for approval. Deliberately the slowest, because learning should integrate over many examples, not lurch per-email.

**None of the loops invokes another.** The fast loop only *writes* the ledger. The slow loop only *reads* the ledger plus sent items. The human loop touches Gmail and one Slack thread per lead. They communicate entirely through shared state, which is why the ledger is designed first, and why the system is robust unattended: any loop can be down, slow, or skipped and the others still function.

---

## The core contract: `process_lead_run`

The body of the fast loop. The keystone unit of work; everything else — what triggers it, where output is stored, who reviews it — is an adapter around it.

```
process_lead_run(task_id, rep_config, trigger_meta) -> LeadRun

input:
  task_id        Salesforce Task id
  rep_config     resolves rep identity: SF OAuth credential + user id, Gmail account,
                 voice-profile ref, signature, per-rep rubric tuning, budget cap
  trigger_meta   source (batch | webhook | manual), timestamp

output (LeadRun, persisted to the ledger):
  route          lead_type + which qualifier handled it
  hard_stops     any categorical ineligibility hits (usually empty)
  dossier        assembled evidence, every claim carrying provenance
  disposition    { call | self_serve | nurture | disqualify }, reasoning, confidence, target
  draft          staged outreach if disposition == call, else null
  status         staged_for_review | blocked | error
```

Nothing in this contract names a person. `rep_config` is what makes it work for one rep now and a team later.

---

## The shell and the qualifier

The shell is a thin deterministic boundary; the qualifier is the agentic interior.

```
fast loop:
  0  poll open tasks, skip task_ids already in the ledger      # dedup        (shell)
  1  crm_context = read(task)                                   #              (shell)
  2  qualifier = registry.dispatch(task, crm_context)           # matching_criteria → qualifier
  3  hard-stops check                                           #              (shell)
  4  result = qualifier.run(task, crm_context, tools)           # researches → judges → drafts
  5  shell.factcheck_at_use(result.draft, result.dossier)       # grounding gate (shell)
  6  shell.stage_to_gmail(result.draft); shell.post_slack(...)  #              (shell)
  7  shell.write_ledger(result, cost, versions)                 #              (shell)
```

Everything between hard-stops and the grounding gate — research, judging, drafting — is the qualifier's interior. The shared primitives (research, drafting) exist as **callable tools** the qualifier orchestrates with its own agentic loop, rather than a fixed sequence the shell runs. This is what keeps per-type qualifiers from becoming copies of one pipeline: the building blocks are shared; only their orchestration is per-type.

Cost is not a constraint on quality. A qualifier gathers evidence across every dimension before judging; the only thing that stops a lead early is a hard-stop, which is correctness, not economy.

---

## The shared toolbox (primitives)

Qualifiers call these tools. Each has a two-layer contract that keeps the system honest:

- **Fetcher layer (deterministic):** Clay enrichment, the PostHog usage query, the Salesforce read. Returns raw data, nothing more.
- **Synthesis layer (Claude):** calls the fetchers and produces assessment. It may only assert things the fetchers actually returned.

Tools return structured evidence with provenance, not prose:

```
Claim {
  field        e.g. "seniority", "monthly_event_volume", "icp_industry_fit"
  value        the assessment or datum
  source       which fetcher / system produced it
  raw          the underlying raw value(s) the claim rests on
  confidence   synthesis-layer confidence
}
```

Synthesis may only emit a Claim whose `raw` traces to fetcher output. That rule is the fact-check checkpoint at creation, and a qualifier is responsible for using these grounded tools to build its dossier.

The tools:

- `crm_context` — Salesforce read. Ground truth; deterministic. For inbound this carries the inbound message text.
- `person_research` — Clay/LinkedIn enrichment → synthesis: seniority, role, budget ownership, likely pain. For product-led leads, used for buying-group discovery (below).
- `company_research` — firmographic + technographic → synthesis: ICP fit, segment, buying signals, stack.
- `usage_research` — PostHog query → synthesis: event volume, products touched, trajectory, activation/expansion signals. For product-led leads it also returns the internal user roster: which people in the account are on PostHog and what each touches.
- `use_case_mapping` — the PostHog use-case-selling check, available for both inbound and product-led leads. A grounded judgment about the probable use case(s) and the PostHog product(s) they map to. For inbound it reasons from the message plus the persona; for an existing account, from the usage patterns.

```
UseCaseClaim {
  use_case      e.g. "debug broken funnels", "safe gradual rollout", "monitor LLM app"
  product       analytics, replay, flags, experiments, surveys, data warehouse,
                LLM analytics, error tracking, web analytics
  evidence      the message phrase / usage rows that ground it
  owner_persona who in the account this use case sits with (product-led)
  confidence
}
```

- `drafter` — given the dossier, disposition, and a framing angle, produces a draft in the rep's voice (below).

A qualifier decides which tools to call and when it has enough — that is the latitude the agentic design buys.

### Product-led: the account is the unit, not the person

The person named on a product-led lead task is an entry point, not the target. They are frequently not the only PostHog user in the account, and often not who to talk to. The trigger (big-fish-on-free, a usage spike, a rolloff) is a statement about the **account** being likely-qualified, not about that individual.

So a product-led qualifier works account-first. `usage_research` surfaces the full internal user roster and what each touches. `person_research` is used for buying-group discovery: enrich the roster, segment by role, identify the probable buyer or champion (who may not be the named lead). `use_case_mapping` says what use cases are live and which persona owns each. The qualifier names the right contact to engage; the draft goes to that person.

---

## Eligibility hard-stops

A small set of categorical correctness checks the shell runs before invoking a qualifier: do-not-contact, a competitor, an account already managed by a teammate, a non-business/personal address. Rare, binary, decided off data. You do not draft outreach to a competitor no matter how good the usage looks. Everything that is not a hard-stop is evidence for the qualifier to weigh.

---

## Qualifiers: one per lead type

Routing dispatches to a **qualifier**, one per (lead_type × matching_criteria), via a declarative **registry**: `matching_criteria → qualifier`. Data-driven dispatch keeps routing deterministic; the registry makes qualifiers pluggable — add a row, add a qualifier, the shell is untouched.

In this org the routing signal is normalized from Salesforce at CRM-read time (`shared/signals.py`), so the registry matches on closed, normalized fields rather than raw text:

- **`category`** — the top level, from the Task `Lead_Source__c` (else the bracketed Subject tag, else the Contact `LeadSource`): `product-led`, `inbound`, `onboarding`, or `outbound`. Inbound channels (contact form, sales mailbox, slack / teams inbound, zendesk, employee referral, lost-opp revival) all collapse to `inbound` because the message is the signal there. `outbound` is rep/tool initiated and is excluded at the poll, so it never reaches routing.
- **`signal`** — for product-led leads only, the sub-signal from the closed `matching_criteria__c` set. Each value maps 1:1 to the product-led qualifier that decides how the lead is handled. The twelve signals: `big_fish`, `mrr_fit`, `job_switcher`, `spend_spike`, `startup_rolloff`, `new_customer`, `recent_fundraise`, `lookalike`, `trust_center_nda`, `unmanaged_ticket`, `scale_activation`, `eng_headcount_growth`. A value not in the set routes to `plg_unclassified`, an account-first fallback that keeps new Salesforce values working until they earn their own signal.

The account-first signals (the account is the unit: usage + roster + buying-group discovery) are `big_fish`, `mrr_fit`, `spend_spike`, `startup_rolloff`, `new_customer`, `unmanaged_ticket`, `scale_activation`. The prospect signals (the named person is the target, with little or no usage to reason from) are `recent_fundraise`, `eng_headcount_growth`, `job_switcher`, `trust_center_nda`, and `lookalike` — where there is no usage at all, so the qualifier judges sales-led potential (can this company reach >$2k MRR quickly?) from company analysis.

A qualifier is an agentic worker — typically a `SKILL.md`-defined skill plus its logic — that owns the interior flow for its lead type. The shell hands it the lead and the toolbox; it researches, assembles a dossier, judges, and drafts. Its one hard obligation is the output contract:

```
Qualifier:
  matches(task, crm_context) -> bool
  run(task, crm_context, tools) -> RunResult
      # tools = { person_research, company_research, usage_research,
      #           use_case_mapping, drafter, ... }
      RunResult {
        dossier:     [ Claims, each with provenance ]
        disposition: Disposition (refs Claims, confidence, target)
        draft:       staged outreach or null
      }
```

However a qualifier runs internally, it must return the same dossier/Claims + Disposition + draft. That conformance is what preserves auditability. Whether the shell can load a `SKILL.md` directly depends on substrate: on the Claude Agent SDK skills load programmatically; on the raw messages API a thin loader reads the `SKILL.md` and injects it.

Example, a product-led big-fish qualifier: it agentically pulls usage heavily, runs buying-group discovery, reasons that a buyable persona is present and the usage trajectory clears the bar despite weak firmographics, names the discovered buyer as the target, and drafts a use-case-led message to that person. All of that orchestration is the qualifier's own; the toolbox and the contract are shared.

---

## The disposition contract

Every qualifier returns its disposition through one shared shape:

```
Disposition {
  disposition   call | self_serve | nurture | disqualify
  reasoning     references specific Claims by id
  confidence    calibrated
  target        for product-led: the contact to actually engage (may differ from the named lead)
}
```

The rubric is holistic — no single axis disqualifies — and a qualifier may tune the bar for its own lead type. Reasoning references Claims by id, which makes the judgment auditable and gives the override loop something concrete to correct. Disposition review is its own artifact: the rep can see the evidence and reasoning and disagree with the *call* even where they'd have taken the same action, and annotate why. That override-plus-rationale is the highest-value training signal in the system, and it arrives through the Slack thread (below), not through sent items.

---

## The fact-check invariant

Grounding is enforced at two points:

- **Creation (inside the qualifier).** The synthesis tools cannot assert a Claim the fetchers did not return, so a qualifier that builds its dossier from the toolbox is building on grounded Claims. The qualifier is responsible for assembling a faithful dossier.
- **Use (at the shell boundary).** Before any draft is staged, the shell runs a verification pass over every factual assertion in the returned draft and confirms each maps to a grounded Claim in the returned dossier. Anything unsupported is flagged or stripped, never sent. This boundary gate runs regardless of how the qualifier produced the draft, so an ungrounded claim cannot reach the rep.

The ledger keeps the provenance, so you can always audit why the agent believed something.

### Conformance suite

Because the shell trusts the interior, every qualifier passes a shared conformance test **before it is registered**: on a fixed test set it returns a well-formed dossier with provenance on every Claim, a Disposition referencing those Claims, a draft that passes the grounding gate, and stays within cost bounds. The suite is what keeps qualifiers faithful as they're added and changed.

---

## The drafter (a shared tool)

```
drafter(dossier, disposition, angle) -> Draft
  voice_profile   living rules doc (per rep): tone, structure, do/don'ts
  exemplar_bank   labeled real sends, retrieved by lead_type
  output          a real Gmail draft in the rep's account, addressed to disposition.target
```

A qualifier calls the drafter with its chosen framing angle. Staging as a real Gmail draft makes the human loop and the voice learning nearly free: the rep edits in place, sends, and the sent copy is the edited draft, so the diff needs no correlation step. The draft leads with the use case and the pain it solves, not a feature list. Voice is a living rules doc plus a bank of labeled real examples retrieved by lead type. Rules anchor; examples teach cadence. No fine-tuning: prompt + profile + exemplars beats it and stays hand-editable.

---

## The human loop: review, send, disagree

Two distinct touchpoints, kept distinct because they carry two different signals.

- **Voice / send → Gmail.** The rep reviews and edits the staged draft where it sits, and sends. The edit is the voice signal.
- **Judgment → the Slack thread.** Watching sent items learns voice but not judgment: a draft that's never sent is ambiguous (bad draft, wrong call, or just a busy day). So disposition disagreement has its own channel: the rep replies in the lead's Slack thread, and the slow loop parses that into `human_disposition` + `human_rationale`. The rep types a note like a human; Claude reads it.

---

## The ledger

The most important artifact, and the medium the loops share. Four jobs at once: **dedup** (the fast loop only processes task_ids not already here), **audit trail**, **learning corpus**, **efficacy analytics**.

```
LeadRun ledger record:
  id, task_id, rep_id, trigger_source, ts
  slack_thread_ref                            # parent message ts of the lead's DM thread
  route:           lead_type, qualifier
  hard_stops:      []
  dossier:         [ Claims, each with provenance ]   # returned by the qualifier
  llm_disposition, llm_reasoning, llm_confidence, target
  human_disposition, human_rationale          # parsed from the lead's Slack thread replies
  staged_draft
  sent_draft                                  # matched from sent items by lead/thread
  draft_diff                                  # staged vs sent — the voice signal
  outcome: replied?, reply_sentiment, meeting_booked?
  cost: per-step model + tokens, tool credits (Clay etc.), run total
  voice_profile_version, rubric_version, model_policy_version
```

Version fields let learning attribute outcomes to the exact profile/rubric/policy that produced a run, and let you roll back. The `cost` block makes spend first-class and auditable — and matters here because an agentic interior can loop.

---

## The slow loop: voice and judgment learning

One nightly job, two sub-loops with identical mechanics, both reading the ledger. Off-hours, propose-then-approve, versioned.

- **Voice sub-loop.** Reads `draft_diff` across runs, separates substantive edits (fact fixes) from stylistic edits (voice) so it learns signal not noise, proposes voice-profile updates. Match the sent item to the lead/thread, not the recipient (outreach can correctly go to a different contact than the named lead), and scope to staged-draft sends only.
- **Judgment sub-loop.** For each lead with replies in its Slack thread (via `slack_thread_ref`), parses the reply into a disposition + rationale, compares against `llm_disposition`, proposes rubric/judge updates from the disagreements.

Three rules for both: track voice fidelity and outreach effectiveness as separate objectives; weight by volume and outcome, not recency; propose-then-approve always — the system never silently updates its own voice or rubric.

---

## The UI: borrowed surfaces

No app. Each loop's human touchpoint borrows a surface already in use.

- **Fast loop output → Gmail + Slack DM.** Drafts land as real Gmail drafts. As each lead is processed it posts to the rep's Slack DM as its own parent message — a compact card (name, company, route, verdict, link to the Gmail draft) — with the full disposition reasoning as a thread reply, so the DM stays scannable. Leads that produced no draft post too, reasoning in-thread. (DM now; a shared channel later — the post target is per-rep config.)
- **Human loop input → Gmail + the thread.** Edit and send in Gmail (voice); reply in the lead's thread to disagree with or annotate the disposition (judgment). Card, reasoning, and override all live in one thread per lead, which is exactly the record the slow loop reads.
- **Slow loop output → markdown diffs.** The voice profile and rubric are markdown files, so the nightly proposals are diffs the rep approves git-style or with a one-line CLI.

A richer review board over ledger data (full dossier + reasoning at a glance) is a possible later addition, not a dependency.

---

## Cost control — model policy and budget governor

A circuit breaker for anomalies, not a throttle on quality. In normal operation it never fires and never downgrades a judgment; it exists to catch the pathological case (a runaway agentic loop, a lead flood, an enrichment retry storm). Cost control lives in the infrastructure layer and never touches the judgment layer.

**Model policy** maps each Claude call to a tier by stakes and difficulty, not a global cost target:

```
routing_fallback     cheap/fast           (Haiku)        low stakes, rare
research synthesis    strong               (Sonnet)       fact-grounding, accuracy matters
qualifier judgment    strongest            (Opus)         highest-stakes decision
drafter               strong               (Sonnet/Opus)  voice fidelity
learning_loops        strongest, batch API (Opus)         off-hours, latency-insensitive
```

Off-hours learning runs through the batch API for a discount at zero quality cost. A qualifier can escalate on uncertainty: if confidence lands low, re-run the judgment on a stronger model rather than ship a shaky call.

**Cost meter.** Every Claude call and paid tool call reports spend into the ledger.

**Budget governor** enforces caps:

- *Per-run cap.* A single lead past a ceiling is almost certainly a runaway agentic loop; hard-stop and flag. This cap matters especially given the agentic interior.
- *Per-day cap.* When hit, the system stops starting new runs and tells the rep — no silent degradation. The rep decides whether to lift it. In-flight runs finish; nothing new starts.
- *Global kill switch.*

The governor wraps each run as middleware, never a step inside the judgment flow.

---

## Config: personal vs. shared

- **Personal (per rep):** voice profile, exemplar bank, identity (SF OAuth credential + user id, Gmail account, Slack post target), signature, per-rep budget cap.
- **Shared (team):** the qualifier registry and qualifier skills, the qualification rubric, the conformance suite, research-tool definitions, hard-stop rules, the ledger schema, the model policy, the team budget cap.

`rep_config` resolves identity at runtime so nothing hardcodes a person.

---

## Authentication: Salesforce

The engine connects to Salesforce over OAuth 2.0.

**Use an External Client App, not a Connected App.** Salesforce disabled creating new Connected Apps by default (newly provisioned orgs from Winter '26, all orgs including existing ones from Spring '26, March 2026). External Client Apps (ECAs) are the replacement: secure-by-default (unusable until granted via a permission set), developer OAuth settings split from admin access policies, modern OAuth flows only. The org admin creates one ECA for the engine and grants it per rep.

**Act as the rep.** The engine authenticates as each rep rather than as a shared integration user. Salesforce's own sharing rules then scope each rep's engine to that rep's leads — no per-rep access control to build — and tasks, notes, and activity stay attributed to the rep. Because the shared SF client handed to a qualifier is already rep-scoped, a qualifier uses that client rather than opening its own connection.

**Flow by phase:** locally, the web-server / authorization-code flow with a refresh token (approve once in a browser, refresh token in the macOS Keychain, silent refresh after). On a multi-rep server, JWT Bearer per rep (certificate-based, pre-authorized once, no interactive step, no refresh token to rotate), same ECA.

**Scopes:** minimal — `api` (REST/SOQL read+write on Task, Lead, Account, Contact) plus `refresh_token`/`offline_access`. **Secrets:** macOS Keychain locally; AWS Secrets Manager (natively supported by ECAs) in the cloud phase.

---

## Adapters: trigger and storage

- `BatchAdapter` — the 5-minute localhost poll. The fast loop, and likely all that's needed.
- `WebhookAdapter` — Salesforce Flow → thin receiver that enqueues; the shell drains the queue. Only if polling proves too slow.
- `ManualAdapter` — run one lead on demand.
- `FilesystemLedger` — SQLite on the Mac (the learning loops query the ledger, not just append). Phase 1.
- `SharedLedger` — Postgres/Supabase, when the team shares it.

---

## Implementation substrate

The shell is a real service (Python or TypeScript) running via launchd. Qualifiers are agentic workers it invokes; the toolbox primitives are functions/tools they call via Claude. The launchd service is the whole home: scheduled, headless, routing to qualifiers, staging Gmail drafts, writing the SQLite ledger, posting Slack, running the nightly learning job. The qualifier prompts/skills and the toolbox are where the quality budget goes.

---

## File / module layout

```
engine/
  loop_fast.*       poll, dedup, dispatch
  router.*          registry → qualifier
  shell.*           hard-stops → qualifier.run → grounding gate → stage → ledger
  factcheck.*       boundary use-gate
  ledger.*          SQLite, dedup, cost fields
  cost.*            governor wraps run()
  loop_slow.*       nightly learning
  slack.*           DM threads
  sf_auth.*         ECA OAuth, Keychain
shared/
  tools/            primitives-as-tools: person, company, usage, use_case, drafter
  signals.py        Salesforce category + product-led signal normalization (closed sets)
  conformance.*     the suite every qualifier must pass
qualifiers/
  registry.yaml     category + signal → qualifier (one rule per qualifier)
  plg_base.py       shared account-first and prospect research flows
  big_fish/         product-led signal qualifier (SKILL.md + thin logic)
  mrr_fit/  spend_spike/  startup_rolloff/  new_customer/      # account-first
  unmanaged_ticket/  scale_activation/
  recent_fundraise/  eng_headcount_growth/  job_switcher/      # prospect
  trust_center_nda/  lookalike/
  plg_unclassified/ product-led fallback for unmapped matching_criteria
  inbound/
  onboarding/
```

The primitives live in `shared/tools/` because qualifiers call them; the engine is a shell.

---

## Build phasing

- **Phase 1 — the fast loop.** The shell, the toolbox, one or two qualifiers, the grounding gate, the conformance suite, the SQLite ledger with dedup, SF auth. Manual + 5-minute batch triggers. Prove disposition quality and voice fidelity on real leads.
- **Phase 1.5 — the human and slow loops.** Gmail draft staging, the Slack DM threads, the nightly learning job, propose-then-approve.
- **Phase 2 — only if needed.** Salesforce Flow → webhook receiver, if polling proves too slow.
- **Phase 3 — team.** Shell to a shared service, ledger to a shared store, JWT-per-rep auth, per-rep config resolved at runtime.

---

## Risks & mitigations

- **Uneven discipline across qualifiers.** Different qualifiers can vary in provenance rigor and dossier richness. *Mitigation:* the boundary grounding gate strips ungrounded output regardless; the conformance suite blocks registration of a qualifier that returns a thin or sloppy dossier; the shared dossier contract is mandatory on output.
- **Non-uniform audit shape.** "Why did it decide this" can vary per type. *Mitigation:* Claims-referencing reasoning is required by the contract.
- **Runaway interior.** An agentic loop can spin. *Mitigation:* the per-run cost cap hard-stops it.

---

## How you'll know it's working

Because every run writes the ledger, quality is observable directly: dispositions the rep agrees with (low override rate), drafts the rep sends with light edits (small `draft_diff`), zero ungrounded facts reaching a draft (the grounding gate at zero leakage), dossiers that pass conformance, runs inside cost and latency bounds. This architecture's strength is flexibility: each qualifier can run whatever research and reasoning its lead type needs, so unusual or edge leads can be handled gracefully and a new lead type is fast to add once its qualifier passes conformance. Watch dossier-completeness variance and per-run cost variance across types, since the agentic interior is where those vary most.

---

## Open decisions

1. **Engine language — Python vs TypeScript.** Gates the first commit. Tied to whether a later review-board app shares code.
2. **Salesforce identity model** — act as each rep (per-rep OAuth; sharing rules scope each engine for free; attribution preserved) vs a single integration user (Client Credentials; simpler unattended but over-permissioned, loses attribution). Leaning act-as-rep.
3. **Auto-stage threshold** — at what qualifier confidence does it stage a draft vs hold for disposition review first?
4. **Exemplar-bank bootstrap** — cold-start voice from existing sent mail, or start empty and accrue? Cold-start is better; the corpus already exists.
5. **Per-rep rubric tuning** — how much divergence from the shared rubric before it fragments into N rubrics.
