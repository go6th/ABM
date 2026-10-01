# Client Data Processing Agreement — Template (v0.9 draft)

> **COUNSEL REVIEW REQUIRED.** This template is a working draft for counsel to redline.
> It is not legal advice and must not be signed before the counsel memo below is completed.

## 1. Parties and roles

- **Company** (us) acts as **processor**; **Client** acts as **controller** for all
  client-supplied personal data and company research outputs generated on the Client's
  instruction.
- Where Company determines the means for its own account research (broker/public data), the
  Company is an independent controller for that pipeline and the transparency duties in
  legal_briefing.md §4 apply.

## 2. Subject matter, duration, nature and purpose

- Subject: the account-intelligence research service (briefs, profiles, watch updates).
- Duration: the term of the master services agreement + 30 d for orderly return/deletion.
- Purpose: producing account research for the Client's sales and marketing use only.

## 3. Data subjects and categories

- Data subjects: company officers and employees in their **business/professional capacity
  only** (names, role titles, employer); no consumer data, no special-category data, no
  private contact details beyond business-published ones.
- Categories: identification data (name, title, employer); company attributes (size,
  industry, funding, tech, hiring, news) derived from public/contracted sources.

## 4. Processor obligations

1. Process only on documented instructions (this DPA + the MSA + feature configuration);
   inform the Client if an instruction infringes data-protection law.
2. Confidentiality undertakings for all personnel; access on a need-to-know basis.
3. Security measures: encryption in transit (TLS 1.2+) and at rest; forced RLS tenant
   isolation (ADR-001); least-privilege runtime roles (NOBYPASSRLS); audit logging of
   privileged actions; annual access review.
4. Subprocessors only per the current register (subprocessor_register.csv) with the notice
   and objection mechanism in §6.
5. Assist the Client with data-subject requests within 10 business days; erasure propagates
   to derived facts on the next re-verification cycle.
6. Breach notification without undue delay and within 48 h of confirmation.
7. Deletion or return of client data at term; derived aggregates that do not identify the
   Client or its data subjects may be retained in de-identified form.
8. International transfers only per the transfer mechanism agreed in Annex II.

## 5. Audit and evidence

Client may request annual evidence of the controls above (penetration-test summary,
isolation-test attestation, RLS verification). On-site audits by prior arrangement,
max once per year, at Client's cost, with 30 days' notice.

## 6. Subprocessor changes

New subprocessors are announced by email **30 days** before onboarding; Client may object
on reasonable data-protection grounds; unresolved objections allow termination of the
affected feature with pro-rata refund.

## 7. Liability

Cap and carve-outs align with the MSA; nothing limits liability for breach of §4.4
(unauthorised subprocessors) or for failure to implement the §4.3 security measures.

## 8. Annex I — Processing details

| Item | Value |
|------|-------|
| Processing purpose | Account research briefs and watch updates |
| Data categories | §3 above |
| Retention | Raw documents 30 d; facts per attribute half-life; account data for account lifetime |
| Locations | Primary region (ap-south-2) plus subprocessor regions per register |

## 9. Annex II — Transfer mechanisms

SCCs (Module 2/3 as applicable) or adequacy where available; transfer impact assessment
appendix to be attached by counsel.

---

## Counsel memo (placeholder — to be completed)

> **COUNSEL MEMO — Date: ____ / ____ / ______**
>
> Reviewer (name, firm): ____________________
>
> - [ ] DPA template reviewed; redlines attached (L-5)
> - [ ] DPDP Act applicability to people data confirmed/corrected (V10, L-3)
> - [ ] GDPR Art. 14(5) balancing test drafted (L-4)
> - [ ] Transfer mechanism for primary region confirmed (Annex II)
> - [ ] Security-measure clause matches implemented controls (RLS, roles, audit)
>
> Memo reference / filing location: ____________________

Until the memo is signed: **no client DPA may be executed**, and broker feeds stay
internal-only (consistent with the T13 live-run blocker).
