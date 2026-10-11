"use client";

import { formatDate, formatMicros } from "@/lib/operator-render";
import { useProviderLabels } from "@/lib/provider-labels";
import { OperatorRequestState } from "../OperatorStates";
import { SupportGrantLookup, type SupportLookup } from "../SupportGrantLookup";
import { useOperatorApi } from "../useOperatorApi";
import { EmptyState, PageHead, Section, Tile } from "@/ui/primitives";

function SpendView({ lookup }: { lookup: SupportLookup }) {
  const { providerName } = useProviderLabels();
  const result = useOperatorApi("GET /operator/v1/runs/{run_id}/spend/live", {
    params: { run_id: lookup.runId ?? "" }, headers: { "X-Support-Grant": lookup.grantId },
  });
  const data = result.data;
  return <>
    <OperatorRequestState loading={result.loading} error={result.error} onRetry={result.reload} />
    {data ? <>
      <div className="grid-4 block-gap">
        <Tile tone="peach" label="Committed" value={formatMicros(data.committed_micros)} note="Committed provider spend" />
        <Tile tone="butter" label="Held" value={formatMicros(data.held_micros)} note="Reserved spend" />
        <Tile tone="mint" label="Remaining" value={formatMicros(data.remaining_micros)} note={`Mode ${data.mode}`} />
        <Tile tone="lilac" label="Cap" value={formatMicros(data.cap_micros)} note={`Version ${data.cap_version ?? "not set"}, as of ${formatDate(data.as_of)}`} />
      </div>
      <Section title="Provider allocation" id="allocation" sub="From the live spend response.">
        {data.by_provider.length ? (
          <div className="table-wrap"><table className="table"><caption className="sr-only">Spend data returned per provider</caption>
            <thead><tr><th scope="col">Provider</th><th scope="col">Response fields</th></tr></thead>
            <tbody>{data.by_provider.map((row, index) => { const code = String(row.provider_code ?? row.provider ?? ("Provider " + (index + 1))); return <tr key={index}><td><strong>{providerName(code)}</strong> <code className="mono t-footnote ink-2">{code}</code></td><td><code className="mono t-footnote">{Object.entries(row).filter(([key]) => key !== "provider_code" && key !== "provider").map(([key, value]) => key + ": " + (typeof value === "string" || typeof value === "number" || typeof value === "boolean" || value === null ? String(value) : "structured value")).join(", ") || "No additional fields"}</code></td></tr>; })}</tbody>
          </table></div>
        ) : <EmptyState title="No provider allocation returned" body="The API returned no provider breakdown for this run." />}
      </Section>
      <Section title="Projection" id="projection">
        <div className="card"><dl className="dl">{Object.entries(data.projection).map(([key, value]) => <div key={key} className="contents"><dt className="mono t-footnote">{key}</dt><dd>{typeof value === "string" || typeof value === "number" || typeof value === "boolean" || value === null ? String(value) : "Structured value returned"}</dd></div>)}</dl></div>
      </Section>
    </> : null}
  </>;
}

export default function OperatorSpendPage() {
  return (
    <div>
      <PageHead title="Live spend" sub="A run's live wallet counters and cap, read-only. Amounts are USD from integer microdollars." />
      <SupportGrantLookup runRequired>{(lookup) => lookup ? <SpendView lookup={lookup} /> : null}</SupportGrantLookup>
    </div>
  );
}
