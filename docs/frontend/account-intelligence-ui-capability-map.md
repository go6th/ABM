# Account Intelligence UI capability map

The client and operator interfaces use the generated API operation catalog as their
implementation boundary. A visible action is enabled only where a corresponding operation
exists; tenant and operator authorization remains enforced by the API.

## Connected client workflows

| Workflow | UI surface | API-backed behavior |
|---|---|---|
| Workspace overview | `/` | Tenant accounts, recent runs, run outcomes, and paginated signals |
| Account directory | `/accounts` | Tenant-scoped search and keyset pagination |
| Account research | `/accounts/{domain}` and account tables | Account data, research tables, source links, table filters, saved views, and exports |
| Research execution | `/runs`, `/runs/{run_id}` | Run list, current run state, progress, coverage, and permitted run actions |
| Research questions | `/chat` | Tenant-scoped threads and streamed answers |
| Signals and alerts | `/alerts`, `/settings/alerts` | Delivered alerts, feedback, subscriptions, and monitoring allowance |
| Tenant administration | `/settings`, `/settings/users`, `/settings/security`, `/settings/connectors`, `/settings/crm`, `/settings/support` | Versioned preferences and branding, members, sessions, authorized connectors, CRM write-back workflow, and support grants |
| Usage and audit | `/usage`, `/audit` | Tenant usage in units and tenant audit records |

The generated operation schemas in `web/lib/gen/api.gen.ts` are the source of truth for
request and response shapes. Product-facing status, grade, progress, and coverage values
must come from the service response; the UI must not infer a successful state from elapsed
time or substitute sample data.

## Explicit capability boundaries

The following requested features are not represented as working client or operator actions
because the current backend contract does not provide their required workflow:

| Requested feature | Current boundary |
|---|---|
| Private Context document vault | Connector consent, allow-lists, and quarantine exist, but there is no tenant document upload, processing, account-mapping, or private-research query API. The existing connector screen must not be described as a complete private-context vault. |
| General account-file import | No tenant account import and column-mapping operation is present. Accounts are added using the supported single-account API. |
| Notification read/unread state and alert detail | The alert contract supports listing and feedback, but not read-state mutation or a dedicated alert-detail operation. |
| Global operator run queue and approval queue | The operator API supports run inspection using a tenant-approved support grant; it intentionally does not expose a global run directory. There is no operator review-queue endpoint. |
| Operator-wide quality, health/SLO, and audit browsing | No corresponding operator read operations are available. Development placeholders are hidden in production. |
| Provider-backed connector ingestion | The connector consent/quarantine workflow exists, but real provider clients and registered OAuth applications are not configured. OAuth or fetch failures must remain visible; a connected UI state is not proof of successful ingestion. |

These limits are also reflected in [the operator route map](./operator-route-map.md), the generated
API contract, and the connector configuration. Do not add demo rows or successful fallbacks
to make an unsupported workflow appear complete.

## Product decisions and role handling

The repository records four tenant roles and the customer-facing usage boundary in
[ADR-0051](../decisions/ADR-0051-tenant-roles.md) and
[ADR-0053](../decisions/ADR-0053-cap-and-credits-in-the-client.md). Both decisions are
marked proposed; the UI follows the implemented API permissions and avoids widening access.
The client displays unit-based usage only, never provider costs or credit balances.

Connector providers and scopes in [the connector configuration](../../config/connectors.yaml)
are proposals and require provider validation. Gmail and call-notes remain disabled. CRM
write-back is opt-in, approval-gated, and limited to the implemented create-only workflow;
outbound prospect outreach is not part of the interface.
