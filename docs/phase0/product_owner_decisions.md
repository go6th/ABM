# T5 — Product-owner decisions: pricing, scope and policy

Ten decisions that gate design and pricing (Spec 19.3 + service-design open items).
Each records the value, rationale and sign-off. **Draft for sign-off** — the signature
lines are the artefact; CI checks their presence.

## PO-1 — Pricing model

**Decision:** per-run credit pricing; 1 credit = £1 nominal list value; standard run =
6 credits, deep run = 18 credits (draft calibration from Spec 16.2 cost per run).
**Rationale:** predictable per-run cost hides LLM/broker variance from customers; the
wallet (ADR-007) settles actual vs reserved.
**Sign-off:** Product owner: ____________________  Date: ______

## PO-2 — Depth tiers

**Decision:** two tiers at launch — `standard` (4 branches) and `deep` (adds refresh
fan-out and more queries); further tiers wait for pilot data.
**Rationale:** matches COST map {standard: 6, deep: 18}; more tiers before measured
economics (T13) would be guessing.
**Sign-off:** Product owner: ____________________  Date: ______

## PO-3 — Regions

**Decision:** single primary region for Phase 0/1 (Mumbai ap-south-1 default, matching
the spec); EU/US add-ons priced separately when the first customer demands it.
**Rationale:** C5 conflict deferred-with-trigger; the schema/RLS design is portable.
**Sign-off:** Product owner: ____________________  Date: ______

## PO-4 — Silo tier availability

**Decision:** not on day one; bridge/silo tiers ship with Phase 3 (T92) and are priced
at cost + margin then.
**Rationale:** spec prices silo "high"; no launch customer has contracted for it yet.
**Sign-off:** Product owner: ____________________  Date: ______

## PO-5 — People-data attributes in scope

**Decision:** name, role/title, organisation, tenure and public profile links only;
no personal contact data (private emails/phones) at launch. Gate: legal memo (T6).
**Rationale:** minimises DPDP/GDPR exposure while keeping the people list useful.
**Sign-off:** Product owner: ____________________  Date: ______

## PO-6 — Operator console

**Decision:** transitional tool — keep through Phase 1 pilot, re-evaluate at Phase 2.
**Rationale:** needed for concierge mode now; not a long-term product surface.
**Sign-off:** Product owner: ____________________  Date: ______

## PO-7 — Phase B connectors (first customer)

**Decision:** CRM CSV export + Microsoft 365 (SharePoint/OneDrive) + call-recording
upload; Drive and live CRM APIs follow.
**Rationale:** matches the first pilot tenant's stated stack; CSV avoids OAuth cost
before the vault proves value.
**Sign-off:** Product owner: ____________________  Date: ______

## PO-8 — Two tenants researching the same account

**Decision:** allowed. Broker payloads on public data may be shared cross-tenant only
where licences permit (T6/T91); private vault data never crosses tenants.
**Rationale:** cost control (brokers are 64–70% of cost) without leaking private data.
**Sign-off:** Product owner: ____________________  Date: ______

## PO-9 — BitScale vs Sumble (people source)

**Decision:** deferred to T13 evidence; the recorded rule is fill-rate first, then cost
per usable senior person; a split by attribute is acceptable.
**Rationale:** both design docs disagree (C9); only measured fill/cost can decide.
**Sign-off:** Product owner: ____________________  Date: ______

## PO-10 — Model split

**Decision:** tiered routing by config (ADR-010); no single-model shortcut; per-step
experiments run in T63 through the eval gate.
**Rationale:** quality per node differs (planner vs extractor vs judge); config keeps
model churn cheap.
**Sign-off:** Product owner: ____________________  Date: ______

---

## Price per run for 70% gross margin (recomputed from Spec 16.2)

Cost per run all-in: MVP **$7.04**, V1 **$5.72**, Scale **$4.68**.
Price for 70% margin = cost / 0.30:

| Tier | Cost per run | Price per run (70% GM) | Credits at £1/$1.25 per credit |
|------|--------------|------------------------|-------------------------------|
| MVP (1k runs/mo) | $7.04 | **$23.48** | ≈ 19 |
| V1 (10k runs/mo) | $5.72 | **$19.08** | ≈ 15 |
| Scale (100k runs/mo) | $4.68 | **$15.62** | ≈ 13 |

Draft credit prices: standard = 6 credits only if measured cost lands ≤ 70% of 6-credit
revenue; T13/T89 update these numbers before self-serve pricing is published.
