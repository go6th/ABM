"""Response models: the typed API contract (D-91 step 0).

Every route declares one of these as its response model, so the OpenAPI snapshot
(``docs/api/openapi.json``) types every field and the web client is generated from it
(``scripts/gen_web_client.py``). The models forbid extra fields: a handler that returns a key the
contract does not name fails its tests instead of leaking an unreviewed field. A field typed
``JsonObject`` is an object whose keys come from configuration (tenant settings, flag rules,
parity tables); the client renders those as data, never as markup.

The models only describe what the handlers already return; they add no value and no field.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JsonObject = dict[str, Any]


class Out(BaseModel):
    """A response body: closed (unknown keys fail validation)."""

    model_config = ConfigDict(extra="forbid")


# ---- probes ---------------------------------------------------------------------------------


class Health(Out):
    """Response body: health."""

    status: str


class Ready(Out):
    """Response body: ready."""

    ready: bool
    checks: dict[str, str]
    # informational status words per external component (never values)
    components: dict[str, str] = {}


# ---- session and identity -------------------------------------------------------------------


class Session(Out):
    """Response body: session."""

    user_id: str
    role: Literal["admin", "analyst", "seller", "viewer"]
    credential: str
    step_up_fresh: bool
    csrf_token: str | None = None


class OperatorSession(Out):
    """The verified 6th staff principal and whether the MFA step-up is fresh."""

    staff_id: str
    role: Literal["operator", "support"]
    mfa_fresh: bool


class SignedOut(Out):
    """Response body: signed out."""

    signed_out: bool


class SessionsEnded(Out):
    """Response body: sessions ended."""

    sessions_ended: int


class SessionSummary(Out):
    """Safe metadata for one active session; never includes the bearer or cookie token."""

    session_id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    amr: list[str]
    current: bool


class SessionList(Out):
    """The active sessions belonging to the verified user in this tenant."""

    sessions: list[SessionSummary]


class EmailVerificationSent(Out):
    """A generic response that does not reveal whether an address has an account."""

    verification_sent: bool


class EmailVerified(Out):
    """One single-use verification token was accepted."""

    verified: bool


class PasswordResetAccepted(Out):
    """Generic acknowledgement for a password reset request."""

    accepted: bool


class PasswordResetCompleted(Out):
    """A single reset token was consumed and the credential was updated."""

    password_updated: bool


class SignedIn(Out):
    """Response body: password sign-in established a session for this tenant."""

    signed_in: bool
    tenant_id: str


class SsoDiscovery(Out):
    """Response body: where a work email signs in. The same shape whether or not a company has a
    connection, so the only thing it tells is "continue with SSO" or "use your password"."""

    next: Literal["sso", "password"]
    start_url: str | None


class TenantChoice(Out):
    """One tenant the verified identity may enter."""

    tenant_id: str
    role: str


class TenantSelectionRequired(Out):
    """Response body (409): the identity belongs to several tenants; resend with one."""

    code: Literal["tenant_selection_required"]
    tenants: list[TenantChoice]


class Me(Out):
    """Authenticated tenant identity and server-evaluated capabilities."""

    user_id: str
    email: str | None
    display_name: str | None
    tenant_id: str
    tenant_name: str
    role: Literal["admin", "analyst", "seller", "viewer"]
    plan: str
    isolation_tier: str
    capabilities: list[str]
    feature_flags: dict[str, bool]
    plan_limits: dict[str, int]
    mfa_enabled: bool
    mfa_required: bool
    mfa_step_up_fresh: bool
    accounts_used: int = 0
    account_cap: int = 0
    accounts_total: int = 0
    mail_drive_allowed: bool = True
    web_refresh_allowed: bool = True
    model_ready: bool = True


class IdpConnection(Out):
    """Response body: idp connection."""

    id: str
    slug: str
    protocol: str
    issuer: str
    client_id: str | None
    authorization_endpoint: str | None
    token_endpoint: str | None
    jwks_uri: str | None
    saml_sso_url: str | None
    sp_entity_id: str | None
    verified_domains: list[str]
    group_role_map: dict[str, str]
    jit_enabled: bool
    require_admin_mfa: bool
    status: str
    version: int
    has_client_secret: bool


class IdpConnections(Out):
    """Response body: idp connections."""

    connections: list[IdpConnection]


class Member(Out):
    """Response body: member."""

    user_id: str
    role: str
    status: str
    version: int


class Members(Out):
    """Response body: members."""

    members: list[Member]


# ---- accounts, runs, progress ---------------------------------------------------------------


class LatestRun(Out):
    """Response body: latest run."""

    run_id: str
    status: str
    created_at: str


class AccountItem(Out):
    """Response body: account item."""

    domain: str
    name: str | None
    latest_run: LatestRun | None
    id: str | None = None
    lanes: list[str] = []
    cadence: str = "off"
    cadence_label: str = "Off"
    last_researched_at: str | None = None
    last_researched_label: str = "Not yet"
    state: Literal[
        "researching",
        "waiting_credit_limit",
        "failed",
        "not_researched",
        "first_scan",
        "new_signals",
        "no_change",
    ] = "not_researched"
    new_signals: int = 0


class AccountPage(Out):
    """Response body: account page."""

    accounts: list[AccountItem]
    next_after: str | None


class AccountAdded(Out):
    """Response body: account added."""

    domain: str
    account_id: str
    created: bool


class AccountRun(Out):
    """Response body: account run."""

    run_id: str
    status: str
    label: str
    mode: str
    created_at: str
    finished_at: str | None


class NarrativeSentence(Out):
    """One sentence of the one-minute narrative and the source it rests on."""

    text: str
    source_code: str | None
    source_title: str | None
    source_url: str | None


class Narrative(Out):
    """The one-minute version of an account."""

    sentences: list[NarrativeSentence]


class FindingRow(Out):
    """A graded finding or change with its date label and source."""

    grade: str | None
    text: str
    when: str
    source_url: str | None
    source_code: str | None


class Changes(Out):
    """What changed in the latest refresh, and the lanes that stayed quiet."""

    since: str
    rows: list[FindingRow]
    quiet_lanes: list[str]


class DeepDiveCounts(Out):
    """Row counts of the deep-dive tables (mail and drive: the caller's own items only; coverage:
    the tracker items Done out of all items of the latest finished run)."""

    signals: int
    people: int
    jobs: int
    tech: int
    news: int
    mail_drive: int
    sources: int
    searches: int = 0
    coverage_done: int = 0
    coverage_total: int = 0


class DeliverableFile(Out):
    """One downloadable deliverable."""

    id: str
    kind: str
    name: str
    filename: str
    meta: str
    built_at: str


class DeliverableFiles(Out):
    """The account's current deliverables."""

    files: list[DeliverableFile]


class StoppedRun(Out):
    """The account's latest research stopped before finishing, or stalled: Resume continues it."""

    run_id: str
    stalled: bool
    reason: str
    steps_done: int
    steps_total: int


class CoverageGap(Out):
    """Steps of the latest research that could not be finished, with the reason in words."""

    reason: str
    count: int


class AccountWorkspace(AccountItem):
    """The Account workspace screen."""

    subline: str
    first_scan: bool
    narrative: Narrative | None
    findings: list[FindingRow]
    changes: Changes | None
    counts: DeepDiveCounts
    files: list[DeliverableFile]
    open_job: OpenJob | None = None
    stopped_run: StoppedRun | None = None
    gaps: list[CoverageGap] = []


class SheetColumn(Out):
    """One column of a deep-dive table."""

    key: str
    label: str
    sortable: bool
    kind: str


class SheetFilter(Out):
    """One filter chip and how many rows it holds."""

    key: str
    label: str
    count: int


class SheetRow(Out):
    """One row: its absolute number, its cells as shown, and its link if it has one."""

    n: int
    cells: dict[str, str]
    link: str | None


class Sheet(Out):
    """One page of a deep-dive table, sorted and filtered on the server."""

    key: str
    title: str
    file: str
    scope: str
    footnote: str
    private: bool
    account_name: str
    columns: list[SheetColumn]
    filters: list[SheetFilter]
    filter: str
    sort: str
    dir: Literal["asc", "desc"]
    total: int
    offset: int
    rows: list[SheetRow]


class Credits(Out):
    """The client's monthly research credit pool."""

    period: str
    pool: int
    used: int
    held: int
    left: int
    resets_on: str


class RefreshOption(Out):
    """One refresh scope with its time and credit estimate."""

    scope: Literal["jobs", "people", "full"]
    name: str
    description: str
    minutes: int
    credits: int
    affordable: bool


class OpenJob(Out):
    """A research job of the account that is waiting, queued or running."""

    id: str
    kind: str
    scope: str
    status: str
    topic: str | None = None
    # live progress: research steps reported so far, of the run's steps (0 before it starts)
    steps_done: int = 0
    steps_total: int = 0


class TopicOption(Out):
    """Research on one topic the user types: its time and credit estimate."""

    minutes: int
    credits: int
    affordable: bool


class RefreshOptions(Out):
    """Everything the Refresh from web screen shows before anything is confirmed."""

    domain: str
    name: str
    refreshed_days_ago: int | None
    allowed: bool
    credits: Credits
    cap_per_pull: int
    options: list[RefreshOption]
    topic: TopicOption
    open_job: OpenJob | None


class RefreshStarted(Out):
    """A confirmed refresh: its job, status and the credits reserved."""

    job_id: str
    status: str
    credits: int


class ResearchEstimate(Out):
    """What researching a new company costs, before anything is confirmed."""

    credits: int
    minutes: int
    pool: Credits
    enough: bool
    accounts_used: int
    account_cap: int


class ResearchStarted(Out):
    """Confirmed new research: its job, status, the credits reserved and the account."""

    job_id: str
    status: str
    credits: int
    domain: str


class DownloadPart(Out):
    """One zip part of a Download everything archive, with its short-lived link."""

    n: int
    name: str
    bytes: int
    url: str


class DownloadJob(Out):
    """A Download everything archive: queued, running, ready (its parts), failed or expired."""

    job_id: str
    status: Literal["queued", "running", "ready", "failed", "expired"]
    domain: str
    files: int
    total_bytes: int
    failure: str | None
    parts: list[DownloadPart]
    links_expire_at: str | None


class ResearchResumed(Out):
    """Resume research: the new job, the stopped run it continues, and its steps already done."""

    job_id: str
    status: str
    resumes_run_id: str
    domain: str
    steps_done: int
    steps_total: int


class LibraryOffer(Out):
    """One ready-made account published to this client."""

    id: str
    name: str
    domain: str
    files: int
    in_workspace: bool


class LibraryAvailable(Out):
    """The ready-made research this client may add, and the caller's remaining places."""

    accounts: list[LibraryOffer]
    accounts_used: int
    account_cap: int
    left: int


class LibraryAdded(Out):
    """What adding from the library did."""

    added: list[str]
    already_in_workspace: int


class LibraryAccountRow(Out):
    """One library account as the operator sees it (no client content)."""

    id: str
    name: str
    domain: str
    deliverables_found: int
    internal_left_out: bool
    check_passed: bool
    issues: list[str]
    uploaded_at: str
    filename: str
    clients: int


class LibraryList(Out):
    """Every current library account."""

    accounts: list[LibraryAccountRow]


class LibraryUploaded(Out):
    """What the upload held and whether it passed the client-ready check."""

    id: str
    name: str
    deliverables_found: int
    internal_left_out: bool
    check_passed: bool
    issues: list[str]


class LibraryPublished(Out):
    """How many clients newly have the account."""

    published: int


class Notification(Out):
    """One in-app notification for the signed-in user."""

    id: str
    kind: str
    created_at: str
    read: bool
    account_domain: str | None
    account_name: str | None
    scope: str | None
    topic: str | None = None
    credits_used: int | None
    reason: str | None


class Notifications(Out):
    """The user's latest notifications and how many are unread."""

    notifications: list[Notification]
    unread: int


class NotificationRead(Out):
    """A notification marked read."""

    read: bool


class ScheduleOption(Out):
    """One cadence on an account's segmented control."""

    cadence: Literal["3d", "weekly", "2w", "monthly", "off"]
    label: str
    allowed: bool


class ScheduleRow(Out):
    """One account's refresh schedule."""

    account_id: str
    domain: str
    name: str
    cadence: Literal["3d", "weekly", "2w", "monthly", "off"]
    next_label: str
    next_refresh_at: str | None
    options: list[ScheduleOption]


class Schedules(Out):
    """Every account's schedule, what it costs a month, and the pool."""

    rows: list[ScheduleRow]
    fastest: str
    fastest_label: str
    estimate_month: int
    pool: int
    resets_on: str


class AccountDetail(Out):
    """Response body: account detail."""

    domain: str
    name: str | None
    added_at: str
    runs: list[AccountRun]


class RunListItem(Out):
    """Response body: run list item."""

    run_id: str
    domain: str
    status: str
    label: str
    created_at: str
    input_mode: str = "company"
    source_label: str = "Public research"


class RunPage(Out):
    """Response body: run page."""

    runs: list[RunListItem]
    next_cursor: str | None


class RunCreated(Out):
    """Response body: run created."""

    run_id: str
    status: str
    replayed: bool


class RunState(Out):
    """Response body: run state."""

    run_id: str
    status: str


class CoverageRow(Out):
    """Response body: coverage row."""

    row: str
    status: str
    priority: str
    reason: str | None
    tools: list[str]
    results: int | None


class CoverageView(Out):
    """Response body: coverage view."""

    rows: list[CoverageRow]
    tally: list[str]


class StoredCounts(Out):
    """Response body: what a run holds so far (counts name their base)."""

    paid_calls: int
    cached_calls: int
    model_replies: int
    facts: int
    drafts: int
    deliverables: int


class MissingRow(Out):
    """Response body: one coverage row that is not done, with the reason."""

    row: str
    status: str
    reason: str | None


class GateDefect(Out):
    """Response body: one pre-delivery gate defect (a check name and an identifier, no text)."""

    check: str
    item: str


class GateSummary(Out):
    """Response body: the run's latest pre-delivery gate verdict and its defects."""

    verdict: str
    defects: list[GateDefect]


class RunStatusView(Out):
    """Response body: run status view."""

    run_id: str
    status: str
    reason: str | None
    coverage: CoverageView
    stop_detail: str | None = None
    stored: StoredCounts | None = None
    missing: list[MissingRow] = []
    domain: str | None = None
    input_mode: str = "company"
    # "Public research" (input mode company) or "From your files" (input mode file)
    source_label: str = "Public research"
    # the other run of an enriched file-mode research: the public run of a file run, or the
    # file run a public enrichment run belongs to
    linked_run_id: str | None = None
    # the latest pre-delivery gate verdict (None: the gate has not run for this run)
    gate: GateSummary | None = None
    # the models the run used, in words ("Research: Opus 5.5 · Analysis: Fable 5.1"), and any
    # step a fallback served (owner decision 2026-10-14); None for a run from before the choice
    models_line: str | None = None


class CollectedFact(Out):
    """Response body: one graded fact a run collected (client-safe fields only)."""

    fact_id: str
    attribute: str
    value: str | None
    grade: str | None
    lane: str | None
    may_state_to_prospect: bool


class CollectedClaim(Out):
    """Response body: one distilled claim and the fact ids it rests on."""

    text: str
    fact_ids: list[str]


class CollectedDrafts(Out):
    """Response body: the client-audience distill output a run produced so far."""

    hypotheses: list[CollectedClaim] = []
    questions: list[CollectedClaim] = []
    narrative: list[CollectedClaim] = []


class CollectedPull(Out):
    """Response body: one finished pull (no provider name; the category and its age)."""

    tool_category: str
    calls: int
    cached: int
    retrieved_from: str | None
    retrieved_to: str | None


class RunCollected(Out):
    """Response body: everything a run holds so far and what is missing and why."""

    run_id: str
    status: str
    reason: str | None
    stop_detail: str | None
    stored: StoredCounts
    pulls: list[CollectedPull]
    facts: list[CollectedFact]
    drafts: CollectedDrafts
    missing: list[MissingRow]


class RunProgress(Out):
    """Response body: run progress."""

    run_id: str
    status: str
    label: str
    stages_done: int
    stages_total: int
    current_stage: str | None
    finished: bool
    text: str


class PullOption(Out):
    """Response body: pull option."""

    available: bool
    pull_run_id: str | None = None
    pulled_at: str | None = None
    rows_stored: int | None = None
    rows_blocked: int | None = None


# ---- tables and saved views -----------------------------------------------------------------


class TableColumn(Out):
    """Response body: table column."""

    key: str
    header: str
    kind: str
    sortable: bool
    operators: list[str]


class RowSource(Out):
    """Response body: row source."""

    code: str | None
    url: str | None
    state: str | None


class TableRow(Out):
    """Response body: table row."""

    position: int
    ref: str | None
    row_id: str | None
    cells: JsonObject
    source: RowSource


class Materialization(Out):
    """Response body: materialization."""

    status: str
    reason: str | None
    counts: dict[str, int]


class TablePage(Out):
    """Response body: table page."""

    run_id: str
    table: str
    materialization: Materialization
    columns: list[TableColumn]
    rows: list[TableRow]
    next_cursor: str | None


class SavedView(Out):
    """Response body: saved view."""

    id: str
    owner: str
    table: str
    name: str
    spec: JsonObject
    shared: bool
    version: int


class SavedViews(Out):
    """Response body: saved views."""

    views: list[SavedView]


class ViewArchived(Out):
    """Response body: view archived."""

    id: str
    archived: bool


# ---- exports --------------------------------------------------------------------------------


class ExportFile(Out):
    """Response body: export file."""

    index: int
    kind: str
    name: str
    sha256: str
    size: int


class ParityVerdict(Out):
    """Response body: parity verdict."""

    ok: bool
    compared: int
    problems: int


class ExportPackage(Out):
    """Response body: export package."""

    package_id: str
    run_id: str
    kind: str
    status: str
    reason: str | None
    parity: dict[str, ParityVerdict] | None
    files: list[ExportFile]
    created_at: str | None = None


class ExportPackages(Out):
    """Response body: export packages."""

    packages: list[ExportPackage]


# ---- settings and branding ------------------------------------------------------------------


class TenantSettings(Out):
    """Response body: tenant settings."""

    version: int
    hash: str | None
    settings: JsonObject


class Branding(Out):
    """Response body: branding."""

    display_name: str | None
    accent: str | None
    logo_sha256: str | None
    attribution: str


class BrandingSaved(Branding):
    """Response body: branding saved."""

    version: int


class LogoUploaded(Out):
    """Response body: logo uploaded."""

    logo_sha256: str
    width: int
    height: int


# ---- audit, usage, support, feedback --------------------------------------------------------


class AuditEvent(Out):
    """Response body: audit event."""

    seq: int
    at: str
    actor: str | None
    actor_kind: str | None
    audit_class: str
    action: str
    resource_type: str | None
    resource_id: str | None
    route: str | None
    request_id: str | None
    outcome: str | None
    support_grant_id: str | None


class AuditPage(Out):
    """Response body: audit page."""

    events: list[AuditEvent]
    next_before_seq: int | None


class UsageEvents(Out):
    """Response body: usage events."""

    kind: str
    events: int
    units: int
    base: str


class UsageItem(Out):
    """Response body: usage item."""

    dimension: str
    used: int
    limit: int | None
    unit: str
    percent: int | None


class Usage(Out):
    """Response body: usage."""

    period: str
    as_of: str
    events: list[UsageEvents]
    items: list[UsageItem]


class SupportGrant(Out):
    """Response body: support grant."""

    id: str
    grantee: str
    scope: str
    reason: str
    granted_by: str
    granted_at: str
    expires_at: str
    revoked_at: str | None


class SupportGrants(Out):
    """Response body: support grants."""

    grants: list[SupportGrant]


class GrantCreated(Out):
    """Response body: grant created."""

    grant_id: str


class GrantRevoked(Out):
    """Response body: grant revoked."""

    grant_id: str
    revoked: bool


class SupportEvent(Out):
    """Response body: support event."""

    grant_id: str
    operator: str
    method: str
    route: str
    outcome: str
    at: str


class SupportEvents(Out):
    """Response body: support events."""

    events: list[SupportEvent]


class FeedbackCreated(Out):
    """Response body: feedback created."""

    id: int | str


class FeedbackItem(Out):
    """Response body: feedback item."""

    id: int | str
    domain: str
    run_id: str | None
    scope: str
    table: str | None
    row_id: str | None
    verdict: str
    reason: str
    note: str | None
    user_id: str
    at: str


class FeedbackList(Out):
    """Response body: feedback list."""

    feedback: list[FeedbackItem]


class UsefulRecorded(Out):
    """Response body: useful recorded."""

    recorded: bool


class Rate(Out):
    """Response body: rate."""

    used: int
    base: int
    small_base: bool
    rate: float | None


class FeedbackCount(Out):
    """Response body: feedback count."""

    verdict: str
    reason: str
    count: int


class UsefulnessMetric(Out):
    """Response body: usefulness metric."""

    window_days: int
    status: str
    question_use: Rate
    confirmed_fact_reference: Rate
    feedback: list[FeedbackCount]


# ---- signals, alerts, monitoring ------------------------------------------------------------


class SignalEvent(Out):
    """Response body: signal event."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_name: str = Field(alias="schema")
    event_id: str
    account: str
    signal_type: str
    fingerprint: str
    grade: str
    lead: bool
    sensitive: bool
    source_codes: list[str]
    score: int
    detected_at: str
    fact_ref: str


class SignalPage(Out):
    """Response body: signal page."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_name: str = Field(alias="schema")
    items: list[SignalEvent]
    next_cursor: str | None


class Alert(Out):
    """Response body: alert."""

    id: str
    rule: str
    tier: str
    lead: bool
    subject: str
    body: str
    created_at: str


class Alerts(Out):
    """Response body: alerts."""

    alerts: list[Alert]


class AlertRated(Out):
    """Response body: alert rated."""

    alert_id: str
    verdict: str


class AlertUsefulness(Out):
    """Response body: alert usefulness."""

    useful: int
    rated: int
    delivered: int
    share: float | None
    small_base: bool
    target: float
    target_is_a_result: bool


class Subscription(Out):
    """Response body: subscription."""

    digest: bool
    immediate_rules: list[str]
    slack: bool
    threshold: int
    muted_accounts: list[str]


class MonitoringBudget(Out):
    """Response body: monitoring budget."""

    period: str
    percent: int


class HistoryItem(Out):
    """Response body: history item."""

    fact_id: str
    signal_type: str
    reason: str
    expired_by_run: str
    expired_at: str
    attribute: str | None
    grade: str | None


class History(Out):
    """Response body: history."""

    history: list[HistoryItem]


# ---- connectors and CRM ---------------------------------------------------------------------


class Connection(Out):
    """Response body: connection."""

    id: str
    kind: str
    status: str
    scopes: list[str]
    broken_reason: str | None
    allowed: list[str]
    quarantined: int
    last_fetch_at: str | None


class Connections(Out):
    """Response body: connections."""

    connections: list[Connection]


class ConnectorStarted(Out):
    """Response body: connector started."""

    connection_id: str
    authorize_url: str


class ConnectorGranted(Out):
    """Response body: connector granted."""

    connection_id: str
    granted: str


class FetchResult(Out):
    """Response body: fetch result."""

    fetched: int
    held: int
    skipped: int
    pages: int
    exit_reason: str | None


class QuarantineItem(Out):
    """Response body: quarantine item."""

    id: str
    resource_id: str
    kind: str
    reason: str
    status: str


class Quarantine(Out):
    """Response body: quarantine."""

    items: list[QuarantineItem]


class QuarantineDecided(Out):
    """Response body: quarantine decided."""

    resource_id: str
    status: str


class Disconnected(Out):
    """Response body: disconnected."""

    connection_id: str
    status: str
    upstream_revocation: str | bool | None


class CrmToggle(Out):
    """Response body: crm toggle."""

    crm: str
    enabled: bool


class CrmMapping(Out):
    """Response body: crm mapping."""

    domain: str
    record_id: str


class CrmPreview(Out):
    """Response body: crm preview."""

    kind: str
    record_id: str
    title: str
    body: str


class CrmWriteRequested(Out):
    """Response body: crm write requested."""

    write_id: str
    new: bool


class CrmApproved(Out):
    """Response body: crm approved."""

    approved: int


class CrmWrite(Out):
    """Response body: crm write."""

    id: str
    kind: str
    title: str
    events: list[str | None]
    created_at: str


class CrmWrites(Out):
    """Response body: crm writes."""

    writes: list[CrmWrite]


# ---- spend (tenant view: no amount or provider; the denylist drop removes the rest) ---------


class CapProposalView(Out):
    """Response body: the latest cap proposal an approver binds to (no provider or line amount)."""

    run_id: str
    version: int
    proposal_hash: str
    state: Literal["proposed", "approved", "rejected", "expired", "extension_declined"]
    cap_micros: int
    calls_by_category: dict[str, int]
    has_exclusions: bool
    has_unknown_costs: bool
    is_extension: bool
    proposed_at: str


class CapApproved(Out):
    """Response body: cap approved."""

    run_id: str
    version: int
    cap_micros: int | None
    resumed: bool


class CapRejected(Out):
    """Response body: cap rejected."""

    run_id: str
    version: int
    decision: str


class CapExtended(Out):
    """Response body: cap extended."""

    run_id: str
    version: int
    status: str


class TenantLiveSpend(Out):
    """Response body: tenant live spend."""

    run_id: str
    status: Literal["within_limit", "near_limit", "waiting_for_approval", "stopped_at_limit"]
    calls_by_category: dict[str, int]
    as_of: str


class PauseApproved(Out):
    """Response body: pause approved."""

    run_id: str
    request_id: str
    cap_micros: int | None
    resumed: bool


class PauseDeclined(Out):
    """Response body: pause declined."""

    run_id: str
    request_id: str
    decision: str
    resumed: bool


# ---- operator plane -------------------------------------------------------------------------


class TenantOverview(Out):
    """Response body: tenant overview."""

    tenant_id: str
    created_at: str
    plan: str | None
    suspended: bool
    members: int
    accounts: int
    live_runs: int


class TenantsOverview(Out):
    """Response body: tenants overview."""

    tenants: list[TenantOverview]


class TenantCreated(Out):
    """Response body: tenant created."""

    tenant_id: str


class TenantLifecycle(Out):
    """Response body: tenant lifecycle."""

    tenant_id: str
    status: str


class QuotaSet(Out):
    """Response body: quota set."""

    dimension: str
    version: int


class PlanSet(Out):
    """Response body: plan set."""

    plan_code: str
    version: int


class DeletionRequested(Out):
    """Response body: deletion requested."""

    request_id: str


class DeletionStep(Out):
    """Response body: deletion step."""

    request_id: str
    plan: str | None = None
    erase: dict[str, int | None] | None = None
    blocked: JsonObject | None = None
    approved_by: str | None = None
    erased: JsonObject | int | None = None
    exit_reason: str | None = None


class Flag(Out):
    """Response body: flag."""

    flag: str
    version: int
    rule: JsonObject


class Flags(Out):
    """Response body: flags."""

    flags: list[Flag]


class FlagSet(Out):
    """Response body: flag set."""

    flag: str
    version: int


class KeyVersion(Out):
    """Response body: key version."""

    provider_code: str
    version: int
    created_by: str
    created_at: str


class KeyVersions(Out):
    """Response body: key versions."""

    keys: list[KeyVersion]


class KeyAdded(Out):
    """Response body: key added."""

    provider_code: str
    version: int


class TimelineUnit(Out):
    """Response body: timeline unit."""

    unit_key: str
    queue_wait_s: float | None
    run_s: float | None
    lease_wait_s: float | None
    outcome: str | None


class RunTimeline(Out):
    """Response body: run timeline."""

    run_id: str
    trace_id: str
    status: str
    created_at: str
    finished_at: str | None
    makespan_s: float | None
    critical_path_s: float | None
    idle_s: float | None
    missing_sources: list[str]
    units: list[TimelineUnit]


class CostLine(Out):
    """Response body: cost line."""

    provider_code: str
    route: str | None
    cost_source: str | None
    calls: int
    cost_micros: int | None
    unknown_cost_calls: int


class Reconcile(Out):
    """Response body: reconcile."""

    rollup_micros: int
    ledger_micros: int
    drift_micros: int
    unknown_cost_calls: int


class RunCost(Out):
    """Response body: run cost."""

    run_id: str
    lines: list[CostLine]
    reconcile: Reconcile


class FailureRow(Out):
    """Response body: failure row."""

    status: str
    reason: str
    runs: int


class Failures(Out):
    """Response body: failures."""

    rows: list[FailureRow]


class StuckRow(Out):
    """Response body: stuck row."""

    run_id: str
    state: str
    reason: str | None
    age_bucket: str | None
    at: str


class Stuck(Out):
    """Response body: stuck."""

    rows: list[StuckRow]


class OperatorLiveSpend(Out):
    """Response body: operator live spend."""

    run_id: str
    as_of: str
    cap_version: int | None
    cap_micros: int | None
    soft_micros: int | None
    committed_micros: int
    held_micros: int
    remaining_micros: int | None
    mode: str
    by_provider: list[JsonObject]
    projection: JsonObject


class UploadTarget(Out):
    """Response body: upload target."""

    url: str
    expires_at: str
    max_bytes: int
    content_type: str


class UploadIntent(Out):
    """Response body: upload intent."""

    intent_id: str
    spec: JsonObject
    upload: UploadTarget


class UploadVerdict(Out):
    """Response body: upload verdict."""

    intent_id: str
    accepted: bool
    reason_code: str | None
    operations: list[str]
    record_count: int | None


class IntentEvent(Out):
    """Response body: intent event."""

    event: str
    reason_code: str | None
    at: str


class IntentEvents(Out):
    """Response body: intent events."""

    intent_id: str
    events: list[IntentEvent]


class ViewAsAccount(Out):
    """Response body: view as account."""

    domain: str
    latest_run_status: str | None


class ViewAsAccounts(Out):
    """Response body: view as accounts."""

    support_grant: str
    accounts: list[ViewAsAccount]


class ViewAsSettings(Out):
    """Response body: view as settings."""

    support_grant: str
    version: int | None
    settings: JsonObject | None
    reason: str | None = None


class ViewAsAccountRow(Out):
    """One account as View as a client shows it: names and counts, never file contents."""

    name: str
    domain: str
    lanes: list[str]
    schedule: str
    last_researched_at: str | None
    people: int
    signals: int
    sources: int


class ViewAsWorkspace(Out):
    """View as a client: the client's workspace, read-only, through an active support grant."""

    support_grant: str
    client: str
    expires_at: str
    users: int
    credits_used: int
    credit_pool: int
    accounts: list[ViewAsAccountRow]


class PilotTenant(Out):
    """Response body: pilot tenant."""

    tenant_id: str
    questions_used: int
    facts_referenced: int
    useful: int
    not_useful: int


class PilotAggregate(Out):
    """Response body: pilot aggregate."""

    tenants: list[PilotTenant]


class ChatModelCapabilities(Out):
    """Public capabilities of a selectable chat model."""

    streaming: bool
    vision: bool
    tool_use: bool


class ChatModel(Out):
    """Public model identifier, prices and capability flags; never provider credentials."""

    id: str
    provider: str
    display_name: str
    input_usd_per_million: str
    output_usd_per_million: str
    cache_write_usd_per_million: str
    cache_read_usd_per_million: str
    context_window: int
    max_output_tokens: int
    capabilities: ChatModelCapabilities


class ChatModels(Out):
    """Available chat models and the configured default."""

    default_model: str
    models: list[ChatModel]


class ChatThreadCreated(Out):
    """A newly created private chat thread."""

    id: str


class ChatThreadSummary(Out):
    """Sidebar summary for one private thread."""

    id: str
    title: str
    turn_count: int
    spent_usd: str
    updated_at: str
    model_id: str | None


class ChatThreads(Out):
    """Recent private chat threads."""

    threads: list[ChatThreadSummary]


class AnswerPart(Out):
    """One run of answer text: plain, bold, or a numbered citation."""

    t: Literal["text", "bold", "cite"]
    v: str


class AnswerSource(Out):
    """A stored row an answer cites: what it is, when, and where to open it."""

    n: int
    title: str
    meta: str
    date: str | None
    url: str | None
    kind: Literal[
        "fact", "job", "person", "signal", "technology", "mail_drive", "file", "tracker", "report"
    ]
    account_domain: str | None
    account_name: str | None


class AnswerItems(Out):
    """A titled list shown in its own card under the answer (for example first call questions)."""

    title: str
    items: list[str]


class AnswerUsed(Out):
    """What one answer drew on, for the "What this answer used" panel."""

    workspace: Literal["used", "not_used"]
    web: Literal["used", "not_used"]
    mail_drive: Literal["used", "not_used", "not_connected"]
    # the model that wrote the answer (owner decision 2026-10-14); None for the workspace's own
    model_name: str | None = None


class AnswerOffer(Out):
    """The "Want anything newer?" card: shown when the workspace is older than the account's
    refresh cadence or could not answer. Nothing goes to the web unless the user confirms."""

    account_domain: str | None
    account_name: str | None
    refreshed_days_ago: int | None


class AnswerStat(Out):
    """One headline number the answer rests on, shown as a chip under the headline."""

    label: str
    value: str


class AnswerSection(Out):
    """One section of a structured answer (render_answer, 2026-10-17): a table with every
    matching row, prose of at most two sentences, one callout, or next steps. Citations in
    the strings are written [n] and number into the answer's sources list."""

    kind: Literal["table", "prose", "callout", "steps"]
    title: str | None = None
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    note: str | None = None
    paragraphs: list[str] | None = None
    text: str | None = None
    tone: Literal["info", "warn", "good"] | None = None
    items: list[str] | None = None


class AnswerStructured(Out):
    """The model's render_answer call, validated and with citations numbered: the UI renders
    these cards and tables; ``paragraphs`` holds the same answer as Markdown."""

    headline: str
    stats: list[AnswerStat]
    sections: list[AnswerSection]


class ChatAnswer(Out):
    """A workspace answer: paragraphs with numbered citations to stored rows."""

    paragraphs: list[list[AnswerPart]]
    structured: AnswerStructured | None = None
    items: AnswerItems | None
    sources: list[AnswerSource]
    used: AnswerUsed
    offer: AnswerOffer | None
    followups: list[str]
    account_domain: str | None
    account_name: str | None
    answered: bool


class ChatTurn(Out):
    """One persisted user/model turn and safe token attribution."""

    id: str
    turn_number: int
    request_id: str
    model_id: str
    user_content: str
    assistant_content: str | None
    client: str
    account_id: str | None
    phase: str
    status: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: str
    created_at: str
    completed_at: str | None
    answer: ChatAnswer | None = None


class ChatThread(Out):
    """Private thread details and chronological turns."""

    id: str
    title: str
    turn_count: int
    spent_usd: str
    reserved_usd: str
    created_at: str
    updated_at: str
    turns: list[ChatTurn]


class ChatThreadDeleted(Out):
    """Confirmation of a caller-owned thread deletion."""

    deleted: bool


class TenantModelKeyStatus(Out):
    """Non-secret status of a tenant's optional model key."""

    available: bool
    configured: bool
    updated_at: str | None


class TenantModelKeyDeleted(Out):
    """Confirmation that a tenant's optional model key was removed."""

    deleted: bool


# ---- uploaded documents (input mode "file") ----------------------------------------------------


class DocumentUploadStarted(Out):
    """Response body: where to send a document's bytes: a resumable storage session (chunks of
    ``chunk_bytes`` with ``Content-Range``), or the API content route where the store has none."""

    document_id: str
    upload_url: str
    method: str
    headers: dict[str, str]
    expires_at: str
    max_bytes: int
    direct: bool
    protocol: Literal["resumable", "single"]
    chunk_bytes: int


class DocumentReceived(Out):
    """Response body: bytes received into quarantine (not yet scanned)."""

    document_id: str
    received_bytes: int


class DocumentPartFailure(Out):
    """A part of a document that could not be read, and why (counted from 1)."""

    part: int
    reason: str


class SkippedMember(Out):
    """A file inside an uploaded zip that was listed but not read, and why."""

    member: str
    reason: str


class DocumentStatus(Out):
    """Response body: one document's processing status (scanning, parsing, indexed, failed)."""

    document_id: str
    account_id: str | None
    name: str
    status: str
    stage: str
    declared_type: str
    detected_type: str | None
    size_bytes: int
    reason: str | None
    message: str | None
    passages: int
    redactions: int
    redaction_tier: str | None
    authoritative: bool
    company: dict[str, Any] | None
    needs_company: bool
    created_at: str
    parts_total: int
    parts_done: int
    parts_failed: list[DocumentPartFailure]
    indexed_passages: int
    members_read: int
    members_skipped: list[SkippedMember]


class DocumentPassage(Out):
    """One passage read from a document, with where it came from."""

    seq: int
    kind: str
    where: str | None
    heading: str | None
    content: str


class DocumentPassages(Out):
    """Response body: a page of what was read from one document."""

    document_id: str
    passages: list[DocumentPassage]
    next_after: int | None


class DriveFile(Out):
    """One file of the caller's Google Drive that a research can read (metadata only)."""

    id: str
    name: str
    type: str
    size_bytes: int | None
    modified_at: str | None
    google_native: bool


class DriveFileList(Out):
    """Response body: the caller's newest readable Drive files."""

    files: list[DriveFile]


class DocumentList(Out):
    """Response body: the tenant's documents."""

    documents: list[DocumentStatus]


class DocumentDeleted(Out):
    """Response body: what a deletion removed and marked."""

    document_id: str
    chunks_removed: int
    facts_marked: int


class CompanyConfirmed(Out):
    """Response body: the account the documents now belong to."""

    account_id: str
    domain: str
    document_ids: list[str]


class DocumentEstimate(Out):
    """Response body: the cost estimate shown before a file-mode run starts."""

    documents: int
    passages: int
    model_calls: int
    input_tokens: int
    output_tokens: int
    model: str | None
    model_usd: float
    embedding_usd: float
    enrichment: bool
    enrichment_cap_usd: float | None
    total_ceiling_usd: float
    note: str
    # what the client sees: research credits (0 for the files alone; the public-sources
    # estimate, confirmed before it runs, when they are on) and the credits left this month
    credits: int
    credits_left: int


class DocumentPolicy(Out):
    """Response body: the tenant's redaction tier."""

    redaction_tier: str


class DocumentFlag(Out):
    """Response body: a document's authoritative flag."""

    document_id: str
    authoritative: bool


class InternalFinding(Out):
    """Response body: one file-derived fact with its grade and citation (Internal Only)."""

    category: str
    value: str
    grade: str
    citation: str
    quote: str


class MailDriveOption(Out):
    """One source the user may connect."""

    kind: str
    label: str
    available: bool


class MailDriveFamily(Out):
    """The Mail card or the Drive card."""

    status: Literal["connected", "not_connected", "not_available", "broken"]
    kind: str | None
    source: str | None
    options: list[MailDriveOption]
    accounts: int
    items: int
    last_synced_at: str | None


class MailAndDrive(Out):
    """The signed-in user's Mail and drive page."""

    allowed: bool
    mail: MailDriveFamily
    drive: MailDriveFamily


class MailDriveConnect(Out):
    """Where to send the browser for the consent."""

    authorize_url: str


class MailDriveDisconnected(Out):
    """What Disconnect did."""

    disconnected: int
    items_deleted: int


class FileAnalysisRun(Out):
    """An account's file analysis run, as the File analysis screen needs it."""

    run_id: str
    account_name: str
    status: str
    done: bool
    created_at: str
    finished_at: str | None
    files: int
    public_run_id: str | None
    # the AI model could not read the files' prose (provider refused or ran out): the page says
    # so instead of "your files do not state anything" (audit 2026-10-15)
    model_unread: bool = False


class InternalView(Out):
    """Response body: the Internal Only view of a run (never in a client file)."""

    run_id: str
    label: str
    source_label: str
    documents: list[dict[str, Any]]
    findings: list[InternalFinding]
    public_run_id: str | None
    public_findings: list[dict[str, Any]]
    file_available: bool


# ---- admin console (A1 to A7): counts and categories only, never question or file text --------


class ConsoleIsolation(Out):
    """The latest nightly isolation check."""

    status: Literal["passed", "failed", "not_run"]
    ran_at: str | None
    workspaces_checked: int
    failures: int


class ConsoleClient(Out):
    """One client as the console lists it."""

    tenant_id: str
    name: str
    users: int
    active_users: int
    accounts: int
    credits_used: int
    credits_held: int
    credit_pool: int
    last_active_at: str | None
    idle_days: int | None
    expires_in_days: int | None
    questions: int
    answered_locally: int
    status: Literal["Invited", "Expiring", "Near credit limit", "Idle", "Active"]


class ConsoleWeek(Out):
    """Questions asked in one week (ISO date of its Monday)."""

    week: str
    questions: int


class ConsoleCategory(Out):
    """Questions in one category (counts only)."""

    key: str
    label: str
    questions: int


class ConsoleClientName(Out):
    """A client for a filter."""

    tenant_id: str
    name: str


class ConsoleOverview(Out):
    """Overview (A1): the last 30 days across clients."""

    clients: int
    active_clients: int
    users: int
    active_users: int
    questions: int
    answered_locally: int
    credits_used: int
    credit_pool: int
    weekly: list[ConsoleWeek]
    weekly_client: str | None
    categories: list[ConsoleCategory]
    not_using: list[ConsoleClient]
    client_names: list[ConsoleClientName]
    isolation: ConsoleIsolation


class ConsoleClients(Out):
    """Clients (A2)."""

    clients: list[ConsoleClient]


class ClientInvite(Out):
    """A new client's (or a renewed) invite link: emailed when a mailer is set up (``emailed``)
    and always returned to copy."""

    tenant_id: str
    email: str
    invite_url: str
    expires_at: str
    emailed: bool = False


class ConsoleClientSettings(Out):
    """A client's limits per user and features."""

    account_cap_per_user: int
    idle_expiry_days: int
    monthly_credit_pool: int
    fastest_schedule: str
    mail_drive_allowed: bool
    web_refresh_allowed: bool
    research_limit_default: int
    chat_limit_default: int
    limit_reset: Literal["monthly", "never"]


class ConsoleLibraryChoice(Out):
    """A passed 6th Library account and whether it is published to this client."""

    library_account_id: str
    name: str
    domain: str
    published: bool


class ConsoleClientDetail(Out):
    """Client detail (A3)."""

    client: ConsoleClient
    settings: ConsoleClientSettings
    models: list[ConsoleModel]
    allowed_models: list[str]
    library: list[ConsoleLibraryChoice]
    user_idle_days: list[int]
    pending_invites: list[str]
    isolation: ConsoleIsolation


class ConsoleUser(Out):
    """One user across clients (A4): counts only."""

    tenant_id: str
    client: str
    user_id: str
    email: str
    role: str
    accounts: int
    account_cap: int
    questions: int
    researches_used: int
    research_limit: int
    chats_used: int
    chat_limit: int
    last_active_at: str | None
    access: Literal["active", "expired", "terminated"]
    ended_at: str | None
    expires_in_days: int | None


class ConsoleUsers(Out):
    """Users (A4)."""

    users: list[ConsoleUser]


class ConsoleLimitRequest(Out):
    """One user's request for more researches or chats (Overview and Users: Requests)."""

    request_id: str
    tenant_id: str
    client: str
    user_id: str
    email: str
    kind: Literal["researches", "chats"]
    note: str | None
    status: Literal["pending", "approved", "declined"]
    created_at: str
    decided_at: str | None
    researches_used: int
    research_limit: int
    chats_used: int
    chat_limit: int


class ConsoleLimitRequests(Out):
    """Pending requests first, then the latest decided ones."""

    requests: list[ConsoleLimitRequest]


class LimitsChanged(Out):
    """A user's limits after a change."""

    user_id: str
    research_limit: int
    chat_limit: int


class AccessChanged(Out):
    """A user's access after Terminate or Re-enable."""

    changed: bool
    access: Literal["active", "expired", "terminated"]


class ConsolePurpose(Out):
    """Credits used this month for one purpose."""

    key: str
    label: str
    credits: int


class ConsoleRun(Out):
    """One research job across clients (A6)."""

    tenant_id: str
    client: str
    job_id: str
    account: str
    type: str
    credits: int
    started_at: str
    status: str


class ConsoleRuns(Out):
    """Runs and spend (A6)."""

    by_purpose: list[ConsolePurpose]
    isolation: ConsoleIsolation
    workspaces: int
    runs: list[ConsoleRun]


class ConsoleAdmin(Out):
    """One 6th admin (from the staff allow-lists)."""

    email: str
    role: Literal["Operator", "Support"]


class LimitUse(Out):
    """One limit this period: used, the limit, what is left."""

    used: int
    limit: int
    left: int


class MyLimits(Out):
    """The caller's research and chat limits (owner decision 2026-10-14)."""

    researches: LimitUse
    chats: LimitUse
    reset: Literal["monthly", "never"]
    resets_on: str | None
    pending_requests: list[Literal["researches", "chats"]]


class LimitRequestSent(Out):
    """A request for more, filed for the 6th admin."""

    request_id: str
    kind: Literal["researches", "chats"]
    status: Literal["pending"]
    created: bool


class ModelChoice(Out):
    """One model in the user's menus: its plain line and, when it cannot be chosen, why."""

    id: str
    label: str
    name: str
    line: str
    available: bool
    reason: str | None


class ModelChoices(Out):
    """The model menus and the user's current research and analysis models."""

    models: list[ModelChoice]
    research: str
    analysis: str
    default_research: str
    default_analysis: str


class ConsoleModel(Out):
    """One choosable model in the console: enabled for the platform, set up (its provider key
    is present), and whether a client must be allowed it (Kimi K3)."""

    id: str
    name: str
    line: str
    enabled: bool
    set_up: bool
    needs_client_permission: bool


class ConsoleDefaults(Out):
    """Platform defaults (A7)."""

    account_cap_per_user: int
    idle_expiry_days: int
    monthly_credit_pool: int
    fastest_schedule: str
    credit_cap_per_pull: int
    models: list[ConsoleModel]
    default_research_model: str
    default_analysis_model: str
    research_limit_default: int
    chat_limit_default: int
    limit_reset: Literal["monthly", "never"]
    max_file_bytes: int
    run_dollar_cap_usd: float
    repeat_call_limit: int
    stall_after_s: int
    model_router_on: bool
    skill_version: str
    skill_installed: bool
    admins: list[ConsoleAdmin]
    updated_by: str
    updated_at: str


AccountWorkspace.model_rebuild()
