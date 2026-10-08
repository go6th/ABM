"use client";

import { formatCount, formatMicros } from "@/lib/operator-render";
import { OperatorRequestState } from "../OperatorStates";
import { SupportGrantLookup, type SupportLookup } from "../SupportGrantLookup";
import { useOperatorApi } from "../useOperatorApi";

function CostView({ lookup }: { lookup: SupportLookup }) {
  const result = useOperatorApi("GET /operator/v1/ops/cost/{run_id}", {
    params: { run_id: lookup.runId ?? "" }, headers: { "X-Support-Grant": lookup.grantId },
  });
  const data = result.data;
  return <>
    <OperatorRequestState loading={result.loading} error={result.error} onRetry={result.reload} />
    {data ? <>
      <section className="operator-section">
        <div className="operator-section-title"><h2>Provider cost lines</h2><span className="muted">{data.lines.length} rows · run {data.run_id}</span></div>
        {data.lines.length ? <div className="operator-table-scroll"><table className="operator-table"><caption className="sr-only">Cost lines per provider and route</caption><thead><tr><th scope="col">Provider</th><th scope="col">Route</th><th scope="col">Source</th><th scope="col">Calls</th><th scope="col">Cost</th><th scope="col">Unknown-cost calls</th></tr></thead><tbody>{data.lines.map((line, index) => <tr key={line.provider_code + ":" + line.route + ":" + index}><td><code>{line.provider_code}</code></td><td>{line.route ?? "Not recorded"}</td><td>{line.cost_source ?? "Not recorded"}</td><td>{formatCount(line.calls)}</td><td>{formatMicros(line.cost_micros)}</td><td>{formatCount(line.unknown_cost_calls)}</td></tr>)}</tbody></table></div> : <div className="empty"><strong>No cost lines returned</strong><p>The API returned no provider cost rows for this run.</p></div>}
      </section>
      <section className="operator-section operator-card"><h2>Ledger reconciliation</h2><dl className="operator-data-list"><div><dt>Cost rollup</dt><dd>{formatMicros(data.reconcile.rollup_micros)}</dd></div><div><dt>Ledger total</dt><dd>{formatMicros(data.reconcile.ledger_micros)}</dd></div><div><dt>Drift</dt><dd>{formatMicros(data.reconcile.drift_micros)}</dd></div><div><dt>Unknown-cost calls</dt><dd>{formatCount(data.reconcile.unknown_cost_calls)}</dd></div></dl></section>
    </> : null}
  </>;
}

export default function OperatorCostPage() {
  return <section>
    <div className="operator-page-heading"><div><p className="eyebrow">TENANT SUPPORT VIEW</p><h1>Run costs</h1><p>Ledger reconciliation and provider-level cost from the operator API.</p></div></div>
    <p className="muted">Amounts are USD converted from integer microdollars. Run IDs and support grants are validated as UUIDs before being sent.</p>
    <div className="operator-section"><SupportGrantLookup runRequired>{(lookup) => lookup ? <CostView lookup={lookup} /> : null}</SupportGrantLookup></div>
  </section>;
}
