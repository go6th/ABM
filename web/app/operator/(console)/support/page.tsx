"use client";
// Support view: a tenant's account domains and settings through a tenant-approved, audited grant.
import { Eye } from "lucide-react";
import { operatorStatus } from "@/lib/operator-render";
import { OperatorRequestState } from "../OperatorStates";
import { SupportGrantLookup, type SupportLookup } from "../SupportGrantLookup";
import { useOperatorApi } from "../useOperatorApi";
import { Badge, Banner, PageHead, Section } from "@/ui/primitives";

function SupportViewData({ lookup }: { lookup: SupportLookup }) {
  const options = { headers: { "X-Support-Grant": lookup.grantId } };
  const accounts = useOperatorApi("GET /operator/v1/view/accounts", options);
  const settings = useOperatorApi("GET /operator/v1/view/settings", options);
  const accountsMatch = accounts.data?.support_grant === lookup.grantId;
  const settingsMatch = settings.data?.support_grant === lookup.grantId;

  return <>
    <div className="block-gap">
      <Banner kind="info" title="Read-only tenant support view" role="status">
        Scoped to one active grant. The tenant sees each request in its support events and audit trail. <code className="mono">{lookup.grantId}</code>
      </Banner>
    </div>
    <Section title="Tenant accounts" id="support-accounts" sub="Domain and latest run state only.">
      <OperatorRequestState loading={accounts.loading} error={accounts.error} empty={Boolean(accounts.data && accounts.data.accounts.length === 0)} onRetry={accounts.reload} />
      {accounts.data && !accountsMatch ? <Banner kind="error" title="Grant response mismatch">Tenant data was not displayed because the API returned a different support grant ID.</Banner> : null}
      {accounts.data && accountsMatch && accounts.data.accounts.length ? (
        <>
          <div className="table-wrap"><table className="table"><caption className="sr-only">Tenant accounts as returned by the read-only support endpoint</caption>
            <thead><tr><th scope="col">Domain</th><th scope="col">Latest run state</th></tr></thead>
            <tbody>{accounts.data.accounts.map((account) => <tr key={account.domain}><td>{account.domain}</td><td>{account.latest_run_status === null ? <span className="ink-2">No run returned</span> : operatorStatus(account.latest_run_status)}</td></tr>)}</tbody>
          </table></div>
          <p className="table-foot">The service caps this response at 200 accounts and does not return a cursor.</p>
        </>
      ) : null}
    </Section>
    <Section title="Tenant settings" id="support-settings" sub="Read-only support response.">
      <OperatorRequestState loading={settings.loading} error={settings.error} onRetry={settings.reload} />
      {settings.data && !settingsMatch ? <p className="inline-error">Settings were withheld because the grant ID did not match.</p> : null}
      {settings.data && settingsMatch ? (
        <div className="card stack">
          <div className="row between"><span className="strong">Settings version {settings.data.version ?? "not available"}</span>{settings.data.reason ? <Badge tone="butter">{settings.data.reason}</Badge> : null}</div>
          {settings.data.settings ? (
            <dl className="dl">{Object.entries(settings.data.settings).map(([key, value]) => <div key={key} className="contents"><dt className="mono t-footnote">{key}</dt><dd>{typeof value === "string" || typeof value === "number" || typeof value === "boolean" || value === null ? String(value) : "Structured setting returned"}</dd></div>)}</dl>
          ) : <p className="ink-2">Settings are not available from the response.</p>}
        </div>
      ) : null}
    </Section>
  </>;
}

export default function OperatorSupportPage() {
  return (
    <div>
      <PageHead title="Support view" sub="Read account identifiers and settings through a tenant-approved, audited grant." />
      <SupportGrantLookup>{(lookup) => lookup ? <SupportViewData lookup={lookup} /> : <p className="row ink-2 block-gap"><Eye size={16} aria-hidden="true" />Nothing is loaded until a grant is checked.</p>}</SupportGrantLookup>
    </div>
  );
}
