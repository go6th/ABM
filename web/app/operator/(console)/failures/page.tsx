"use client";

import { formatCount, formatDate } from "@/lib/operator-render";
import { OperatorRequestState } from "../OperatorStates";
import { SupportGrantLookup, type SupportLookup } from "../SupportGrantLookup";
import { useOperatorApi } from "../useOperatorApi";

function FailureData({ lookup }: { lookup: SupportLookup }) {
  const options = { headers: { "X-Support-Grant": lookup.grantId } };
  const failures = useOperatorApi("GET /operator/v1/ops/failures", options);
  const stuck = useOperatorApi("GET /operator/v1/ops/stuck", options);
  return <>
    <section className="operator-section">
      <div className="operator-section-title"><h2>Failed and partial runs</h2><span className="muted">Grouped counts · tenant-approved scope</span></div>
      <OperatorRequestState loading={failures.loading} error={failures.error} empty={Boolean(failures.data && failures.data.rows.length === 0)} onRetry={failures.reload} />
      {failures.data && failures.data.rows.length ? <div className="operator-table-scroll"><table className="operator-table"><caption className="sr-only">Run counts grouped by status and failure reason</caption><thead><tr><th scope="col">Status</th><th scope="col">Reason</th><th scope="col">Runs</th></tr></thead><tbody>{failures.data.rows.map((row, index) => <tr key={row.status + ":" + row.reason + ":" + index}><td>{row.status}</td><td><code>{row.reason}</code></td><td>{formatCount(row.runs)}</td></tr>)}</tbody></table></div> : null}
    </section>
    <section className="operator-section">
      <div className="operator-section-title"><h2>Stuck-run observations</h2><span className="muted">Latest backend observations</span></div>
      <OperatorRequestState loading={stuck.loading} error={stuck.error} empty={Boolean(stuck.data && stuck.data.rows.length === 0)} onRetry={stuck.reload} />
      {stuck.data && stuck.data.rows.length ? <div className="operator-table-scroll"><table className="operator-table"><caption className="sr-only">Latest stuck run observations with identifiers and reasons</caption><thead><tr><th scope="col">Run ID</th><th scope="col">State</th><th scope="col">Reason</th><th scope="col">Age bucket</th><th scope="col">Observed</th></tr></thead><tbody>{stuck.data.rows.map((row) => <tr key={row.run_id + ":" + row.at}><td><code>{row.run_id}</code></td><td>{row.state}</td><td>{row.reason ? <code>{row.reason}</code> : "Not recorded"}</td><td>{row.age_bucket ?? "Not recorded"}</td><td>{formatDate(row.at)}</td></tr>)}</tbody></table></div> : null}
    </section>
  </>;
}

export default function OperatorFailuresPage() {
  return <section>
    <div className="operator-page-heading"><div><p className="eyebrow">TENANT SUPPORT VIEW</p><h1>Failures & stuck runs</h1><p>Read-only, tenant-scoped operational summaries.</p></div></div>
    <p className="muted">The operator API groups failed runs by status and reason; it does not expose global failure aggregates.</p>
    <div className="operator-section"><SupportGrantLookup>{(lookup) => lookup ? <FailureData lookup={lookup} /> : null}</SupportGrantLookup></div>
  </section>;
}
