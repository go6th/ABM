"use client";

import { formatCount, formatDate } from "@/lib/operator-render";
import { OperatorRequestState } from "../OperatorStates";
import { SupportGrantLookup, type SupportLookup } from "../SupportGrantLookup";
import { useOperatorApi } from "../useOperatorApi";
import { PageHead, Section } from "@/ui/primitives";
import { humanize } from "@/lib/ws-format";

function FailureData({ lookup }: { lookup: SupportLookup }) {
  const options = { headers: { "X-Support-Grant": lookup.grantId } };
  const failures = useOperatorApi("GET /operator/v1/ops/failures", options);
  const stuck = useOperatorApi("GET /operator/v1/ops/stuck", options);
  return <>
    <Section title="Failed and partial runs" id="failed" sub="Counts grouped by status and reason, within the grant's tenant.">
      <OperatorRequestState loading={failures.loading} error={failures.error} empty={Boolean(failures.data && failures.data.rows.length === 0)} onRetry={failures.reload} />
      {failures.data && failures.data.rows.length ? (
        <div className="table-wrap"><table className="table"><caption className="sr-only">Run counts grouped by status and failure reason</caption>
          <thead><tr><th scope="col">Status</th><th scope="col">Reason</th><th scope="col" className="num">Runs</th></tr></thead>
          <tbody>{failures.data.rows.map((row, index) => <tr key={row.status + ":" + row.reason + ":" + index}><td>{humanize(row.status)}</td><td><code className="mono t-footnote">{row.reason}</code></td><td className="num">{formatCount(row.runs)}</td></tr>)}</tbody>
        </table></div>
      ) : null}
    </Section>
    <Section title="Stuck run observations" id="stuck" sub="The latest observations from the service.">
      <OperatorRequestState loading={stuck.loading} error={stuck.error} empty={Boolean(stuck.data && stuck.data.rows.length === 0)} onRetry={stuck.reload} />
      {stuck.data && stuck.data.rows.length ? (
        <div className="table-wrap"><table className="table"><caption className="sr-only">Latest stuck run observations with identifiers and reasons</caption>
          <thead><tr><th scope="col">Run ID</th><th scope="col">State</th><th scope="col">Reason</th><th scope="col">Age</th><th scope="col">Observed</th></tr></thead>
          <tbody>{stuck.data.rows.map((row) => <tr key={row.run_id + ":" + row.at}><td><code className="mono t-footnote">{row.run_id}</code></td><td>{humanize(row.state)}</td><td>{row.reason ? <code className="mono t-footnote">{row.reason}</code> : <span className="ink-2">Not recorded</span>}</td><td>{row.age_bucket ?? <span className="ink-2">Not recorded</span>}</td><td className="nowrap">{formatDate(row.at)}</td></tr>)}</tbody>
        </table></div>
      ) : null}
    </Section>
  </>;
}

export default function OperatorFailuresPage() {
  return (
    <div>
      <PageHead title="Failures and stuck runs" sub="Read-only, tenant-scoped summaries. There are no global failure totals." />
      <SupportGrantLookup>{(lookup) => lookup ? <FailureData lookup={lookup} /> : null}</SupportGrantLookup>
    </div>
  );
}
