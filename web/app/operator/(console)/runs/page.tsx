"use client";

import { formatDate, formatDuration } from "@/lib/operator-render";
import { OperatorRequestState } from "../OperatorStates";
import { SupportGrantLookup, type SupportLookup } from "../SupportGrantLookup";
import { useOperatorApi } from "../useOperatorApi";

function RunTimelineView({ lookup }: { lookup: SupportLookup }) {
  const result = useOperatorApi("GET /operator/v1/runs/{run_id}/timeline", {
    params: { run_id: lookup.runId ?? "" }, headers: { "X-Support-Grant": lookup.grantId },
  });
  const data = result.data;
  return (
    <>
      <OperatorRequestState loading={result.loading} error={result.error} onRetry={result.reload} />
      {data ? <>
        <div className="operator-grid operator-section">
          <article className="operator-card"><span className="eyebrow">RUN STATUS</span><strong className="operator-card-value">{data.status}</strong><span className="operator-card-caption">Created {formatDate(data.created_at)}</span></article>
          <article className="operator-card"><span className="eyebrow">MAKESPAN</span><strong className="operator-card-value">{formatDuration(data.makespan_s)}</strong><span className="operator-card-caption">Critical path {formatDuration(data.critical_path_s)}</span></article>
          <article className="operator-card"><span className="eyebrow">IDLE TIME</span><strong className="operator-card-value">{formatDuration(data.idle_s)}</strong><span className="operator-card-caption">Trace <code>{data.trace_id}</code></span></article>
        </div>
        <section className="operator-section">
          <div className="operator-section-title"><h2>Run units</h2><span className="muted">Event-derived timings · {data.units.length} rows</span></div>
          {data.missing_sources.length ? <p className="banner tone-butter">Some event sources are unavailable: {data.missing_sources.join(", ")}</p> : null}
          {data.units.length ? <div className="operator-table-scroll"><table className="operator-table"><caption className="sr-only">Run unit queue, execution and lease timings</caption><thead><tr><th scope="col">Unit</th><th scope="col">Outcome</th><th scope="col">Queue wait</th><th scope="col">Run time</th><th scope="col">Lease wait</th></tr></thead><tbody>{data.units.map((unit, index) => <tr key={unit.unit_key + ":" + index}><td><code>{unit.unit_key}</code></td><td>{unit.outcome ?? "Not recorded"}</td><td>{formatDuration(unit.queue_wait_s)}</td><td>{formatDuration(unit.run_s)}</td><td>{formatDuration(unit.lease_wait_s)}</td></tr>)}</tbody></table></div> : <div className="empty"><strong>No unit timings returned</strong><p>The API returned the timeline with no unit rows.</p></div>}
        </section>
      </> : null}
    </>
  );
}

export default function OperatorRunsPage() {
  return <section>
    <div className="operator-page-heading"><div><p className="eyebrow">TENANT SUPPORT VIEW</p><h1>Run inspection</h1><p>Inspect one run through an active tenant-approved support grant.</p></div></div>
    <div className="banner tone-sky operator-section" role="note"><span aria-hidden="true">ⓘ</span><div className="banner-body"><strong>No global run directory is exposed</strong><p>The API returns a timeline only for a run ID and tenant grant you provide. Reads are audited against that tenant.</p></div></div>
    <div className="operator-section"><SupportGrantLookup runRequired>{(lookup) => lookup ? <RunTimelineView lookup={lookup} /> : null}</SupportGrantLookup></div>
  </section>;
}
