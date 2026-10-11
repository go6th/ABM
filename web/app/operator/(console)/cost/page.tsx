"use client";

import { formatCount, formatMicros } from "@/lib/operator-render";
import { useProviderLabels } from "@/lib/provider-labels";
import { OperatorRequestState } from "../OperatorStates";
import { SupportGrantLookup, type SupportLookup } from "../SupportGrantLookup";
import { useOperatorApi } from "../useOperatorApi";
import { EmptyState, PageHead, Section, Tile } from "@/ui/primitives";

function CostView({ lookup }: { lookup: SupportLookup }) {
  const { providerName } = useProviderLabels();
  const result = useOperatorApi("GET /operator/v1/ops/cost/{run_id}", {
    params: { run_id: lookup.runId ?? "" }, headers: { "X-Support-Grant": lookup.grantId },
  });
  const data = result.data;
  return <>
    <OperatorRequestState loading={result.loading} error={result.error} onRetry={result.reload} />
    {data ? <>
      <Section title="Provider cost lines" id="lines" sub={`${data.lines.length} rows for run ${data.run_id}`}>
        {data.lines.length ? (
          <div className="table-wrap"><table className="table"><caption className="sr-only">Cost lines per provider and route</caption>
            <thead><tr><th scope="col">Provider</th><th scope="col">Route</th><th scope="col">Source</th><th scope="col" className="num">Calls</th><th scope="col" className="num">Cost</th><th scope="col" className="num">Unknown cost calls</th></tr></thead>
            <tbody>{data.lines.map((line, index) => <tr key={line.provider_code + ":" + line.route + ":" + index}><td><strong>{providerName(line.provider_code)}</strong> <code className="mono t-footnote ink-2">{line.provider_code}</code></td><td>{line.route ?? <span className="ink-2">Not recorded</span>}</td><td>{line.cost_source ?? <span className="ink-2">Not recorded</span>}</td><td className="num">{formatCount(line.calls)}</td><td className="num">{formatMicros(line.cost_micros)}</td><td className="num">{formatCount(line.unknown_cost_calls)}</td></tr>)}</tbody>
          </table></div>
        ) : <EmptyState title="No cost lines returned" body="The API returned no provider cost rows for this run." />}
      </Section>
      <Section title="Ledger reconciliation" id="reconcile">
        <div className="grid-4">
          <Tile tone="peri" label="Cost rollup" value={formatMicros(data.reconcile.rollup_micros)} />
          <Tile tone="lilac" label="Ledger total" value={formatMicros(data.reconcile.ledger_micros)} />
          <Tile tone={data.reconcile.drift_micros === 0 ? "mint" : "rose"} label="Drift" value={formatMicros(data.reconcile.drift_micros)} />
          <Tile tone="butter" label="Unknown cost calls" value={formatCount(data.reconcile.unknown_cost_calls)} />
        </div>
      </Section>
    </> : null}
  </>;
}

export default function OperatorCostPage() {
  return (
    <div>
      <PageHead title="Unit costs" sub="Ledger reconciliation and provider cost for one run. Amounts are USD from integer microdollars." />
      <SupportGrantLookup runRequired>{(lookup) => lookup ? <CostView lookup={lookup} /> : null}</SupportGrantLookup>
    </div>
  );
}
