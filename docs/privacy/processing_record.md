# Processing record (template for counsel)

**Status of this document: template. It has not been completed by counsel and is
not legal advice.** The shape follows an Article 30 style processing record;
counsel decides the final form.

| Item | Value |
|---|---|
| Controller and processor roles per jurisdiction | **not completed** |
| Categories of data subjects | **not completed** |
| Categories of personal data | see `config/minimisation_map.yaml` (the map is the single source) |
| Purposes | account research deliverables (brief, people list, alerts) |
| Recipients (sub-processors) | see `docs/privacy/subprocessors.md` |
| Transfers and safeguards | **not completed** |
| Retention | see `config/retention.yaml` (windows carry their sources) |
| Security measures | tenant isolation (RLS), audit log, minimisation, suppression |
| Erasure process | `src/privacy/dsar.py` (access and deletion requests) |

Nothing in this record states that a law is satisfied.
