"use client";

import { operatorStatus } from "@/lib/operator-render";
import { OperatorRequestState } from "../OperatorStates";
import { SupportGrantLookup, type SupportLookup } from "../SupportGrantLookup";
import { useOperatorApi } from "../useOperatorApi";

function SupportViewData({ lookup }: { lookup: SupportLookup }) {
  const options = { headers: { "X-Support-Grant": lookup.grantId } };
  const accounts = useOperatorApi("GET /operator/v1/view/accounts", options);
  const settings = useOperatorApi("GET /operator/v1/view/settings", options);
  const accountsMatch = accounts.data?.support_grant === lookup.grantId;
  const settingsMatch = settings.data?.support_grant === lookup.grantId;

  return <>
    <div className="support-view-banner" role="status">
      <span aria-hidden="true">◉</span>
      <div><strong>Read-only tenant support view</strong><p>This access is scoped to one active tenant grant. The tenant can see each support request in its support events and audit log.</p></div>
      <code>{lookup.grantId}</code>
    </div>
    <section className="operator-section">
      <div className="operator-section-title"><h2>Tenant accounts</h2><span className="muted">Domain and latest run state only</span></div>
      <OperatorRequestState loading={accounts.loading} error={accounts.error} empty={Boolean(accounts.data && accounts.data.accounts.length === 0)} onRetry={accounts.reload} />
      {accounts.data && !accountsMatch ? <div className="operator-gate-message" role="alert"><strong>Grant response mismatch</strong><span>Tenant data was not displayed because the API returned a different support grant ID.</span></div> : null}
      {accounts.data && accountsMatch && accounts.data.accounts.length ? <div className="operator-table-scroll"><table className="operator-table"><caption className="sr-only">Tenant accounts as returned by the read-only support endpoint</caption><thead><tr><th scope="col">Domain</th><th scope="col">Latest run state</th></tr></thead><tbody>{accounts.data.accounts.map((account) => <tr key={account.domain}><td>{account.domain}</td><td>{account.latest_run_status === null ? "No run returned" : operatorStatus(account.latest_run_status)}</td></tr>)}</tbody></table><p className="operator-table-foot">The service caps this response at 200 accounts and does not return a cursor.</p></div> : null}
    </section>
    <section className="operator-section">
      <div className="operator-section-title"><h2>Tenant settings</h2><span className="muted">Read-only support response</span></div>
      <OperatorRequestState loading={settings.loading} error={settings.error} onRetry={settings.reload} />
      {settings.data && !settingsMatch ? <p className="operator-action-message">Settings were withheld because the grant ID did not match.</p> : null}
      {settings.data && settingsMatch ? <div className="operator-card"><div className="operator-section-title"><span>Settings version {settings.data.version ?? "not available"}</span>{settings.data.reason ? <span className="badge tone-butter">{settings.data.reason}</span> : null}</div>{settings.data.settings ? <dl className="operator-data-list">{Object.entries(settings.data.settings).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "string" || typeof value === "number" || typeof value === "boolean" || value === null ? String(value) : "Structured setting returned"}</dd></div>)}</dl> : <p className="muted">Settings are not available from the response.</p>}</div> : null}
    </section>
  </>;
}

export default function OperatorSupportPage() {
  return <section>
    <div className="operator-page-heading"><div><p className="eyebrow">TENANT APPROVED ACCESS</p><h1>Support view</h1><p>Read account identifiers and settings through a tenant-approved, audited grant.</p></div></div>
    <div className="operator-section"><SupportGrantLookup>{(lookup) => lookup ? <SupportViewData lookup={lookup} /> : null}</SupportGrantLookup></div>
  </section>;
}
