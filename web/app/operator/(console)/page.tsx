"use client";

import Link from "next/link";
import { useOperatorViewer } from "./OperatorSessionGate";
import { OperatorRequestState, DevelopmentOnlyNotice } from "./OperatorStates";
import { useOperatorApi } from "./useOperatorApi";
import { formatCount, formatDate } from "@/lib/operator-render";
import { useProviderLabels } from "@/lib/provider-labels";

export default function OperatorHomePage() {
  const { providerName } = useProviderLabels();
  const viewer = useOperatorViewer();
  const isOperator = viewer.role === "operator";
  const tenants = useOperatorApi("GET /operator/v1/tenants");
  const flags = useOperatorApi("GET /operator/v1/flags", {}, isOperator);
  const keys = useOperatorApi("GET /operator/v1/keys", {}, isOperator);
  const pilot = useOperatorApi("GET /operator/v1/pilot/aggregate", {}, isOperator);
  const tenantRows = tenants.data?.tenants ?? [];
  const activeTenants = tenantRows.filter((tenant) => !tenant.suspended).length;
  const liveRuns = tenantRows.reduce((total, tenant) => total + tenant.live_runs, 0);
  const countText = (value: number | null, loading: boolean, error: unknown) =>
    error ? "Unavailable" : loading ? "…" : value === null ? "—" : formatCount(value);

  return (
    <section>
      <div className="operator-page-heading">
        <div><p className="eyebrow">PLATFORM OPERATIONS</p><h1>Operations overview</h1><p>Platform health and tenant administration at a glance.</p></div>
        <Link className="btn primary" href="/operator/tenants">Manage tenants <span aria-hidden="true">↗</span></Link>
      </div>

      <section className="operator-overview-stats" aria-label="Live operator summary">
        <article className="operator-overview-stat">
          <span className="operator-overview-icon tone-lavender"><span aria-hidden="true">◫</span></span>
          <div><span className="eyebrow">TENANTS</span><strong>{countText(tenants.data?.tenants.length ?? null, tenants.loading, tenants.error)}</strong></div>
          <span className="operator-overview-caption">Provisioned workspaces</span>
        </article>
        <article className="operator-overview-stat">
          <span className="operator-overview-icon tone-mint"><span aria-hidden="true">✓</span></span>
          <div><span className="eyebrow">ACTIVE</span><strong>{countText(tenants.data ? activeTenants : null, tenants.loading, tenants.error)}</strong></div>
          <span className="operator-overview-caption">Active tenants</span>
        </article>
        <article className="operator-overview-stat">
          <span className="operator-overview-icon tone-sky"><span aria-hidden="true">↗</span></span>
          <div><span className="eyebrow">LIVE RUNS</span><strong>{countText(tenants.data ? liveRuns : null, tenants.loading, tenants.error)}</strong></div>
          <span className="operator-overview-caption">Across tenant workspaces</span>
        </article>
        <article className="operator-overview-stat">
          <span className="operator-overview-icon tone-peach"><span aria-hidden="true">⚑</span></span>
          <div><span className="eyebrow">FEATURE FLAGS</span><strong>{isOperator ? countText(flags.data?.flags.length ?? null, flags.loading, flags.error) : "Restricted"}</strong></div>
          <span className="operator-overview-caption">{isOperator ? "Configured platform flags" : "Operator role required"}</span>
        </article>
      </section>

      <div className="operator-overview-note" role="note"><span aria-hidden="true">i</span><p>Summary values come directly from the operator API. Tenant metadata is content-free; customer account content is never shown here.</p></div>

      <section className="operator-section" aria-labelledby="tenant-overview-title">
        <div className="operator-section-title"><h2 id="tenant-overview-title">Tenant metadata</h2><Link href="/operator/tenants">Manage tenants →</Link></div>
        <p className="muted">Content-free overview from the operator API. Tenant names and account content are not returned here.</p>
        <OperatorRequestState loading={tenants.loading} error={tenants.error} empty={Boolean(tenants.data && tenants.data.tenants.length === 0)} onRetry={tenants.reload} />
        {tenants.data && tenants.data.tenants.length > 0 ? (
          <div className="operator-table-scroll">
            <table className="operator-table">
              <caption className="sr-only">Tenant identifiers and server-provided plan, lifecycle and counts</caption>
              <thead><tr><th scope="col">Tenant ID</th><th scope="col">Plan</th><th scope="col">Lifecycle</th><th scope="col">Members</th><th scope="col">Accounts</th><th scope="col">Live runs</th></tr></thead>
              <tbody>{tenants.data.tenants.slice(0, 8).map((tenant) => (
                <tr key={tenant.tenant_id}>
                  <td><code>{tenant.tenant_id}</code></td>
                  <td>{tenant.plan ?? "Unknown"}</td>
                  <td><span className={`badge ${tenant.suspended ? "tone-rose" : "tone-mint"}`}>{tenant.suspended ? "Suspended" : "Active"}</span></td>
                  <td>{formatCount(tenant.members)} members</td>
                  <td>{formatCount(tenant.accounts)} accounts</td>
                  <td>{formatCount(tenant.live_runs)} live runs</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : null}
      </section>

      {isOperator ? (
        <div className="operator-grid operator-section">
          <section className="operator-card" aria-labelledby="flags-title">
            <div className="operator-section-title"><h2 id="flags-title">Feature flag versions</h2><span className="badge tone-sky">Server data</span></div>
            <OperatorRequestState loading={flags.loading} error={flags.error} empty={Boolean(flags.data && flags.data.flags.length === 0)} onRetry={flags.reload} />
            {flags.data && flags.data.flags.length > 0 ? <ul className="operator-compact-list">{flags.data.flags.slice(0, 6).map((flag) => <li key={flag.flag}><span>{flag.flag}</span><code>v{formatCount(flag.version)}</code></li>)}</ul> : null}
          </section>
          <section className="operator-card" aria-labelledby="keys-title">
            <div className="operator-section-title"><h2 id="keys-title">Key references</h2><span className="badge tone-slate">Write-only values</span></div>
            <OperatorRequestState loading={keys.loading} error={keys.error} empty={Boolean(keys.data && keys.data.keys.length === 0)} onRetry={keys.reload} />
            {keys.data && keys.data.keys.length > 0 ? <ul className="operator-compact-list">{keys.data.keys.slice(0, 6).map((key) => <li key={`${key.provider_code}:${key.version}`}><span>{providerName(key.provider_code)}<small><code>{key.provider_code}</code> · Added {formatDate(key.created_at)}</small></span><code>v{formatCount(key.version)}</code></li>)}</ul> : null}
          </section>
        </div>
      ) : (
        <div className="banner tone-sky operator-section" role="status"><span aria-hidden="true">ⓘ</span><div className="banner-body"><strong>Support view</strong><p>Tenant lifecycle and quota changes, flags, and key references are restricted to operator staff.</p></div></div>
      )}

      {isOperator ? (
        <section className="operator-section" aria-labelledby="pilot-title">
          <div className="operator-section-title"><h2 id="pilot-title">Pilot feedback signals</h2><span className="muted">Counts returned by the service</span></div>
          <OperatorRequestState loading={pilot.loading} error={pilot.error} empty={Boolean(pilot.data && pilot.data.tenants.length === 0)} onRetry={pilot.reload} />
          {pilot.data && pilot.data.tenants.length > 0 ? (
            <div className="operator-table-scroll"><table className="operator-table">
              <caption className="sr-only">Per-tenant feedback event counts</caption>
              <thead><tr><th scope="col">Tenant ID</th><th scope="col">Questions used events</th><th scope="col">Facts referenced events</th><th scope="col">Useful feedback events</th><th scope="col">Not-useful feedback events</th></tr></thead>
              <tbody>{pilot.data.tenants.map((row) => <tr key={row.tenant_id}><td><code>{row.tenant_id}</code></td><td>{formatCount(row.questions_used)}</td><td>{formatCount(row.facts_referenced)}</td><td>{formatCount(row.useful)}</td><td>{formatCount(row.not_useful)}</td></tr>)}</tbody>
            </table></div>
          ) : null}
        </section>
      ) : null}

      <DevelopmentOnlyNotice title="Additional overview endpoints are not connected" detail="Run totals by state, stuck-run observations, spend caps, alert freshness, telemetry health and review queue age are not available from the current operator contract." />
    </section>
  );
}
