"use client";

import Link from "next/link";
import { useOperatorViewer } from "./OperatorSessionGate";
import { OperatorRequestState, DevelopmentOnlyNotice } from "./OperatorStates";
import { useOperatorApi } from "./useOperatorApi";
import { formatCount, formatDate } from "@/lib/operator-render";

export default function OperatorHomePage() {
  const viewer = useOperatorViewer();
  const isOperator = viewer.role === "operator";
  const tenants = useOperatorApi("GET /operator/v1/tenants");
  const flags = useOperatorApi("GET /operator/v1/flags", {}, isOperator);
  const keys = useOperatorApi("GET /operator/v1/keys", {}, isOperator);
  const pilot = useOperatorApi("GET /operator/v1/pilot/aggregate", {}, isOperator);

  return (
    <section>
      <div className="operator-page-heading">
        <div><p className="eyebrow">PLATFORM OPERATIONS</p><h1>Overview</h1><p>Verified staff access with an audit trail.</p></div>
        <Link className="btn" href="/operator/tenants">Open tenant administration</Link>
      </div>

      <div className="operator-grid">
        <article className="operator-card">
          <span className="eyebrow">STAFF ID</span>
          <strong className="operator-card-value mono">{viewer.staffId}</strong>
          <span className="operator-card-caption">Identity returned by the staff verifier</span>
        </article>
        <article className="operator-card">
          <span className="eyebrow">ACCESS ROLE</span>
          <strong className="operator-card-value">{isOperator ? "Operator" : "Support"}</strong>
          <span className="operator-card-caption">Role claim checked by the API</span>
        </article>
        <article className="operator-card">
          <span className="eyebrow">MFA</span>
          <strong className="operator-card-value">Fresh</strong>
          <span className="operator-card-caption">Verified against the configured step-up window</span>
        </article>
      </div>

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
            {keys.data && keys.data.keys.length > 0 ? <ul className="operator-compact-list">{keys.data.keys.slice(0, 6).map((key) => <li key={`${key.provider_code}:${key.version}`}><span><code>{key.provider_code}</code><small>Added {formatDate(key.created_at)}</small></span><code>v{formatCount(key.version)}</code></li>)}</ul> : null}
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
