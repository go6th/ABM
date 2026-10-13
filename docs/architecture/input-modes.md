# Two research input modes

A run researches a company in one of two input modes, chosen on the New research screen and in
`POST /v1/runs`. Exactly one mode per run; a mixed or empty body is refused (422
`input_mode_mixed`, `input_mode_empty`).

| | Company name ("Public research") | Upload file ("From your files") |
|---|---|---|
| Body | `{"input_mode": "company", "company": {"company_name", "domain"?, "credit_cap"?}}` (the legacy `{"domain"}` body still works) | `{"input_mode": "file", "file": {"document_ids", "account_id"?, "enrich_public": false, "credit_cap"?}}` |
| Pipeline | The existing one, unchanged (Workflow v1, or the ABM graph behind its flag) | `documents.graph` (setup, extract, tracker, internal file, gate, deliver, finish) |
| Sources | Public sources through the connector ledger and cap approval | The uploaded documents only; the ledger holds zero-cost document reads (`p_docs`), never a broker call |
| Grades | Confirmed, Probable, Hypothesis, Lead | Internal (Confirmed only from a document an admin marked authoritative), always private |
| Output | Client file set (public facts only) | The Internal Only view and Word file, labelled "Internal: from your uploaded material" |
| Tracker | 33 rows from ledger proof | 33 rows: Done from document reads, "Not in uploaded data", or "Not searched (file-only mode)" |

`enrich_public` (off by default) also creates a linked company-mode run on the same account with
the credit cap as its budget and its own cap approval; its findings appear beside the file
findings, labelled "Public research".

## Document pipeline (`src/documents/`)

```
POST /v1/documents/uploads  (name, size and type checked; quarantine key; one-time token)
PUT  signed URL or /v1/documents/{id}/content?token=   (bytes into quarantine only)
POST /v1/documents/{id}/complete   (size and sha256) -> task /tasks/document-process
  type from content -> malware scan (fail closed) -> promote out of quarantine
  -> parse in the sandbox subprocess (no network, no processes, no writes, limits)
  -> company evidence (raw) -> redact (tenant tier) -> chunk -> document_passages + redaction map
  -> bound to an account: embed -> chunks (private index, tenant and account)
```

Statuses: `awaiting_upload`, `quarantined`, `scanning`, `rejected` (with a reason, never parsed),
`parsing`, `parsed` (waiting for the company), `indexing`, `indexed`, `failed`. A document
uploaded without an account stops at `parsed` with the detected company; the user confirms it
(`POST /v1/documents/confirm-company`). A run refuses unconfirmed documents with 409
`company_confirmation_required` and the candidates: the company is never guessed.

Chunks: documents 400 to 800 tokens with about 12% overlap and the heading path; call
transcripts in 60 to 90 second windows of whole speaker turns; CRM and spreadsheet rows one per
passage. Every fact cites its document and the passage location (page, slide, section, row of a
sheet, or the transcript time range).

## Safety boundaries

* Untrusted content: passages reach the model only inside escaped `<document>` tags with
  instructions never to follow them; the model has no tools; a fact is kept only when its quote
  is found verbatim in its passage (`documents.extract`).
* Two outputs: file-derived facts are `private`; the pre-delivery gate (`documents.gate`) leak
  checks every client file against the account's private passages (shared 8-word runs and
  embedding similarity) and records each check in `leak_checks`.
* Deletion removes the index rows, passages, redaction map and stored objects and marks every
  fact that cites the document `needs_reevaluation` (`fact_reevaluations`).
* Roles: admins and sellers upload; viewers cannot upload or open the Internal Only view. Every
  upload, parse, run start, enrichment opt-in, internal view access and deletion is audited.

Open decisions: `docs/open-items.md`, section "Two research input modes".
