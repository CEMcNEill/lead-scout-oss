# Inbound qualifier

## When this handles a lead
An inbound inquiry: the lead arrived because the person reached out (form, demo
request, contact-us), so the task carries an inbound message and the named
person is who to talk to.

## How it works
1. Read the CRM record for ground truth and the inbound message text.
2. Enrich the named person for seniority, role, budget ownership, likely pain.
3. Enrich the company for ICP fit, segment, signals, stack.
4. Map the use case from the message plus the persona and company. The message
   is the strongest evidence of intent, so it leads the mapping.
5. If the lead already maps to a PostHog account, fold in usage.
6. Judge holistically against the rubric. Target is the named lead.
7. If the disposition is call, draft a message that leads with the use case the
   message implies and the pain it solves.

## Notes
Every dossier Claim carries provenance; the message phrase that grounds a use
case is captured as evidence. The draft is checked at the shell boundary, so no
fact that is not grounded in a Claim survives to staging.
