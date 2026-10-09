"use client";

import { formatDate, formatMicros } from "@/lib/operator-render";
import { providerName } from "@/lib/provider-labels";
import { OperatorRequestState } from "../OperatorStates";
import { SupportGrantLookup, type SupportLookup } from "../SupportGrantLookup";
import { useOperatorApi } from "../useOperatorApi";

function SpendView({ lookup }: { lookup: SupportLookup }) {
  const result = useOperatorApi("GET /operator/v1/runs/{run_id}/spend/live", {
    params: { run_id: lookup.runId ?? "" }, headers: { "X-Support-Grant": lookup.grantId },
  });
  const data = result.data;
  return <>
    <OperatorRequestState loading={result.loading} error={result.error} onRetry={result.reload} />
    {data ? <>
      <div className="operator-grid operator-section">
        <article className="operator-card"><span className="eyebrow">COMMITTED</span><strong className="operator-card-value">{formatMicros(data.committed_micros)}</strong><span className="operator-card-caption">Committed provider spend</span></article>
        <article className="operator-card"><span className="eyebrow">HELD</span><strong className="operator-card-value">{formatMicros(data.held_micros)}</strong><span className="operator-card-caption">Reserved spend</span></article>
        <article className="operator-card"><span className="eyebrow">REMAINING</span><strong className="operator-card-value">{formatMicros(data.remaining_micros)}</strong><span className="operator-card-caption">Mode: {data.mode}</span></article>
        <article className="operator-card"><span className="eyebrow">CAP</span><strong className="operator-card-value">{formatMicros(data.cap_micros)}</strong><span className="operator-card-caption">Version {data.cap_version ?? "not set"} · as of {formatDate(data.as_of)}</span></article>
      </div>
      <section className="operator-section">
        <div className="operator-section-title"><h2>Provider allocation</h2><span className="muted">Live spend response</span></div>
        {data.by_provider.length ? <div className="operator-table-scroll"><table className="operator-table"><caption className="sr-only">Spend data returned per provider</caption><thead><tr><th scope="col">Provider</th><th scope="col">Response fields</th></tr></thead><tbody>{data.by_provider.map((row, index) => { const code = String(row.provider_code ?? row.provider ?? ("Provider " + (index + 1))); return <tr key={index}><td><strong>{providerName(code)}</strong> <code>{code}</code></td><td><code>{Object.entries(row).filter(([key]) => key !== "provider_code" && key !== "provider").map(([key, value]) => key + ": " + (typeof value === "string" || typeof value === "number" || typeof value === "boolean" || value === null ? String(value) : "[structured value]")).join(" · ") || "No additional fields"}</code></td></tr>; })}</tbody></table></div> : <div className="empty"><strong>No provider allocation returned</strong><p>The API returned no provider breakdown for this run.</p></div>}
      </section>
      <section className="operator-section operator-card"><h2>Projection fields</h2><dl className="operator-data-list">{Object.entries(data.projection).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "string" || typeof value === "number" || typeof value === "boolean" || value === null ? String(value) : "Structured value returned"}</dd></div>)}</dl></section>
    </> : null}
  </>;
}

export default function OperatorSpendPage() {
  return <section>
    <div className="operator-page-heading"><div><p className="eyebrow">TENANT SUPPORT VIEW</p><h1>Live spend</h1><p>Review the run’s live wallet counters and cap from the backend.</p></div></div>
    <p className="muted">Amounts are USD converted from integer microdollars. This screen is read-only.</p>
    <div className="operator-section"><SupportGrantLookup runRequired>{(lookup) => lookup ? <SpendView lookup={lookup} /> : null}</SupportGrantLookup></div>
  </section>;
}
