"use client";
// Operations overview: tenant counts, the tenant table by id, flag and key summaries, and pilot
// feedback counts. Everything comes from the operator API; tenant metadata is content-free.
import Link from "next/link";
import { Activity, CheckCircle2, Flag, Store } from "lucide-react";
import { useOperatorViewer } from "./OperatorSessionGate";
import { OperatorRequestState } from "./OperatorStates";
import { useOperatorApi } from "./useOperatorApi";
import { formatCount, formatDate } from "@/lib/operator-render";
import { useProviderLabels } from "@/lib/provider-labels";
import { Badge, Banner, PageHead, Section, Tile } from "@/ui/primitives";

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
    error ? "Unavailable" : loading ? "Loading" : value === null ? "None" : formatCount(value);

  return (
    <div>
      <PageHead
        title="Operations overview"
        sub="Tenants, configuration and pilot signals at a glance. No client content is shown here."
        actions={<Link className="btn primary" href="/operator/tenants">Manage tenants</Link>}
      />

      <section className="grid-4" aria-label="Live operator summary">
        <Tile tone="lilac" icon={<Store className="glyph" size={20} aria-hidden="true" />} label="Tenants" value={countText(tenants.data?.tenants.length ?? null, tenants.loading, tenants.error)} note="Provisioned workspaces" />
        <Tile tone="mint" icon={<CheckCircle2 className="glyph" size={20} aria-hidden="true" />} label="Active" value={countText(tenants.data ? activeTenants : null, tenants.loading, tenants.error)} note="Not suspended" />
        <Tile tone="peri" icon={<Activity className="glyph" size={20} aria-hidden="true" />} label="Live runs" value={countText(tenants.data ? liveRuns : null, tenants.loading, tenants.error)} note="Across all tenants" />
        <Tile tone="peach" icon={<Flag className="glyph" size={20} aria-hidden="true" />} label="Feature flags" value={isOperator ? countText(flags.data?.flags.length ?? null, flags.loading, flags.error) : "Restricted"} note={isOperator ? "Configured flags" : "Operator role required"} />
      </section>

      <Section title="Tenants" id="tenant-overview" sub="Identifiers and counts only. The newest eight are shown." action={<Link className="link t-subhead" href="/operator/tenants">All tenants</Link>}>
        <OperatorRequestState loading={tenants.loading} error={tenants.error} empty={Boolean(tenants.data && tenants.data.tenants.length === 0)} onRetry={tenants.reload} />
        {tenants.data && tenants.data.tenants.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Tenant identifiers and server-provided plan, lifecycle and counts</caption>
              <thead><tr><th scope="col">Tenant ID</th><th scope="col">Plan</th><th scope="col">Lifecycle</th><th scope="col" className="num">Members</th><th scope="col" className="num">Accounts</th><th scope="col" className="num">Live runs</th></tr></thead>
              <tbody>{tenants.data.tenants.slice(0, 8).map((tenant) => (
                <tr key={tenant.tenant_id}>
                  <td><code className="mono t-footnote">{tenant.tenant_id}</code></td>
                  <td>{tenant.plan ?? "Unknown"}</td>
                  <td><Badge tone={tenant.suspended ? "rose" : "mint"} dot>{tenant.suspended ? "Suspended" : "Active"}</Badge></td>
                  <td className="num">{formatCount(tenant.members)}</td>
                  <td className="num">{formatCount(tenant.accounts)}</td>
                  <td className="num">{formatCount(tenant.live_runs)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : null}
      </Section>

      {isOperator ? (
        <div className="grid-2 section">
          <section className="card" aria-labelledby="flags-title">
            <div className="row between"><h2 id="flags-title" className="card-title">Feature flags</h2><Link className="link t-subhead" href="/operator/configuration">Manage</Link></div>
            <OperatorRequestState loading={flags.loading} error={flags.error} empty={Boolean(flags.data && flags.data.flags.length === 0)} onRetry={flags.reload} />
            {flags.data && flags.data.flags.length > 0 ? (
              <ul className="group gap-top">{flags.data.flags.slice(0, 6).map((flag) => (
                <li key={flag.flag} className="group-item"><span className="grow mono t-footnote">{flag.flag}</span><Badge tone="peri">Version {formatCount(flag.version)}</Badge></li>
              ))}</ul>
            ) : null}
          </section>
          <section className="card" aria-labelledby="keys-title">
            <div className="row between"><h2 id="keys-title" className="card-title">Key references</h2><Badge>Values are write-only</Badge></div>
            <OperatorRequestState loading={keys.loading} error={keys.error} empty={Boolean(keys.data && keys.data.keys.length === 0)} onRetry={keys.reload} />
            {keys.data && keys.data.keys.length > 0 ? (
              <ul className="group gap-top">{keys.data.keys.slice(0, 6).map((key) => (
                <li key={`${key.provider_code}:${key.version}`} className="group-item">
                  <span className="grow"><span className="title">{providerName(key.provider_code)}</span><br /><span className="sub"><code className="mono">{key.provider_code}</code>, added {formatDate(key.created_at)}</span></span>
                  <Badge tone="peri">Version {formatCount(key.version)}</Badge>
                </li>
              ))}</ul>
            ) : null}
          </section>
        </div>
      ) : (
        <div className="section"><Banner kind="info" title="Support view" role="status">Tenant lifecycle and quota changes, flags and key references are restricted to operator staff.</Banner></div>
      )}

      {isOperator ? (
        <Section title="Pilot feedback signals" id="pilot" sub="Event counts by tenant, as the service returns them.">
          <OperatorRequestState loading={pilot.loading} error={pilot.error} empty={Boolean(pilot.data && pilot.data.tenants.length === 0)} onRetry={pilot.reload} />
          {pilot.data && pilot.data.tenants.length > 0 ? (
            <div className="table-wrap"><table className="table">
              <caption className="sr-only">Per-tenant feedback event counts</caption>
              <thead><tr><th scope="col">Tenant ID</th><th scope="col" className="num">Questions used</th><th scope="col" className="num">Facts referenced</th><th scope="col" className="num">Useful</th><th scope="col" className="num">Not useful</th></tr></thead>
              <tbody>{pilot.data.tenants.map((row) => <tr key={row.tenant_id}><td><code className="mono t-footnote">{row.tenant_id}</code></td><td className="num">{formatCount(row.questions_used)}</td><td className="num">{formatCount(row.facts_referenced)}</td><td className="num">{formatCount(row.useful)}</td><td className="num">{formatCount(row.not_useful)}</td></tr>)}</tbody>
            </table></div>
          ) : null}
        </Section>
      ) : null}
    </div>
  );
}
