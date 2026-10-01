# 09 Private context and the two-output rule (SAAS 7, ARCH 8)

Phase B (weeks 20 to 28 in ARCH) lets a tenant add its own material (CRM exports, call recordings and transcripts, email threads, proposals, notes) so research blends public evidence with what the client already knows, **without any of it leaking to another tenant or into a client-facing file**. Do not start Phase B until the isolation suite has run green in production (ARCH 17.1).

## The two-output rule (applies to every tenant, from the first release)
| Output | Audience | Contains |
| --- | --- | --- |
| Client-facing file set | Anyone the client shares with, sometimes the account itself | **Public evidence only.** Internal material appears only in neutral form: "Client", "call notes" with a date, "email" by date and subject with no link. No people names from internal sources, no recordings, no links to internal systems |
| Internal view (**Internal Only**) | Named users inside the tenant with the internal role | Everything: internal passages, people named, redaction map in reverse, relationship state per person, full source list. Portal view plus a separate Word file marked Internal Only on the cover and in the header, **never placed in the shared folder or the workspace zip** |

Enforcement is layered, not a convention:
1. `facts.visibility` (public or private) and `chunks.visibility = 'private'` (CHECK constraint).
2. `render_public` runs as the `public_reader` DB role: **no grant on `chunks`, and a policy that only exposes `visibility = 'public'` facts.** Scope every other policy on those tables `TO app_rw` (see `13-starter-code-and-known-defects.md`, defect 2: an unscoped policy leaks private rows to `public_reader`).
3. **Leak detector** before any public artefact is released: scan for names, emails and identifiers from the tenant's redaction maps and for any internal source id. A hit **blocks delivery** the way a broker name does today. `scripts/scan_client_output.py --extra <terms-file>` is the file-level scanner; the production detector is `src/render/leakcheck.py`.
4. Planted canary documents and vectors in every test tenant; leakage canaries must stay at zero.
5. The internal Word file is rendered by a different code path to a different storage location than the shared set.

## Ingestion pipeline (ARCH 8.1, SAAS 7.3)
Connect (OAuth connectors or presigned multipart upload straight to S3, so large files cannot hurt API p99; **nothing is ingested without a user in the tenant having authorised the source**) → Quarantine (quarantine bucket, virus scan, size and type check; **nothing is parsed before the scan passes**) → Parse (document parser; speech recognition with speaker diarization when only audio exists; **parsers run in a sandboxed pod with no network except the vendor**) → Classify (type, date, account mapping confirmed by the client where ambiguous, sensitivity) → Redact (per-tenant policy, Presidio plus custom recognisers; **store the map, keep the original**) → Chunk → Embed (1536-d, batched, retried, rate limited, stored with model and version) → Index (tenant partition, under tenant context, visibility fixed to private).
Then extract candidate facts with the model, each graded **Internal** and linked to its passage.

## Redaction is a tenant policy, not a blanket rule
| Tier | Redacted | Kept | Default for |
| --- | --- | --- | --- |
| Strict | All personal identifiers including names and emails | Roles and organisation names | Regulated tenants |
| Standard | Government IDs, payment cards, health data, credentials, bank details | Business contact names, titles, work emails | Everyone (default) |
| Minimal | Credentials and payment cards only | Everything else | Opt-in with a data processing addendum |

## Chunking (ARCH 8.3)
Documents: structure-aware, 400 to 800 tokens, 10 to 15% overlap, heading path prepended. Call transcripts: windows of speaker turns of about 60 to 90 seconds with speaker labels, call date and opportunity stage as metadata. CRM notes: one note per chunk with account, stage, owner, date. Fallback for unstructured text: 512-token window with 50-token overlap.

## Retrieval and use (ARCH 8.4)
Scope is tenant (RLS) **and** account (filter) on every query. Hybrid fusion (vector top 50 + full-text top 50, reciprocal rank fusion), rerank, return the top 8 with document id and character span. **Private snippets enter prompts only inside the quarantine boundary** (documents can contain instructions too). Every private statement in the internal view carries a citation to its document and span.

## How research uses it (SAAS 7.4)
Pass 1 (relationship and history) is answered from the vault first. Pass 2 hypotheses are tested against internal evidence; a grade rises only when a public source or an authoritative internal source supports it, and the reasoning states which. The people list gains relationship state per person (met, emailed, presented to, no contact, with dates). Question answering runs over both evidence sets with the source class on every answer: Public (with link) or Internal (document and date). The role decides whether internal passages are shown or only their existence.

## Deletion, quality, security
Deleting a document removes its chunks and S3 object and marks derived facts for re-evaluation; **propagation within 24 hours**. Track recall@8, nDCG@10 and citation precision per tenant sample, plus planted canaries. Per-tenant KMS key with an encryption context on every object; offboarding by crypto-shredding. Call audio is kept only if the client asks. Confirm zero-retention and no-training terms with the LLM, embedding and speech vendors **before** Phase B (**[VERIFY] V5**), and speech pricing and diarization quality on real calls (**V6**). Path to SOC 2 Type I in the first year of the enhanced version.

## Client-history lookups (routing)
`client_history` in `assets/routing.yaml` is answered from tenant connectors and needs the pursuing client name. People names from it stay in the Internal Only view.
