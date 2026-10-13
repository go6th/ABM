"use client";
// Overview: counts of active clients and users, live runs, questions by category only, and the
// isolation check status. Counts and categories only, never question text.
import { useOperatorApi } from "./useOperatorApi";
import { Loading, Notice, PageHead, Status } from "@/ui/primitives";

export default function OperatorOverview() {
  const tenants = useOperatorApi("GET /operator/v1/tenants");
  const pilot = useOperatorApi("GET /operator/v1/pilot/aggregate");
  const stuck = useOperatorApi("GET /operator/v1/ops/stuck");
  if (tenants.error) return <Notice kind="error">{tenants.error.detail}</Notice>;
  if (!tenants.data) return <Loading />;
  const rows = tenants.data.tenants;
  const active = rows.filter((t) => !t.suspended);
  const users = rows.reduce((n, t) => n + t.members, 0);
  const liveRuns = rows.reduce((n, t) => n + t.live_runs, 0);
  const questions = (pilot.data?.tenants ?? []).reduce((n, t) => n + t.questions_used, 0);
  const useful = (pilot.data?.tenants ?? []).reduce((n, t) => n + t.useful, 0);
  const notUseful = (pilot.data?.tenants ?? []).reduce((n, t) => n + t.not_useful, 0);
  const referenced = (pilot.data?.tenants ?? []).reduce((n, t) => n + t.facts_referenced, 0);
  return (
    <>
      <PageHead title="Overview" sub="Counts only. Question text and client files never appear here." />
      <dl className="stats">
        <div><dt className="label">Active clients</dt><dd className="value">{active.length}<span className="small muted"> of {rows.length}</span></dd></div>
        <div><dt className="label">Users</dt><dd className="value">{users}</dd></div>
        <div><dt className="label">Runs now</dt><dd className="value">{liveRuns}</dd></div>
        <div><dt className="label">Questions asked</dt><dd className="value">{pilot.data ? questions : "—"}</dd></div>
      </dl>
      <section className="section">
        <h2>What people ask about</h2>
        {pilot.data ? (
          <ul className="rows">
            <li><span>Answers marked useful</span><span className="end">{useful}</span></li>
            <li><span>Answers marked not useful</span><span className="end">{notUseful}</span></li>
            <li><span>Facts referenced</span><span className="end">{referenced}</span></li>
          </ul>
        ) : pilot.error ? <p className="muted small">Usage categories are unavailable right now.</p> : <Loading />}
      </section>
      <section className="section">
        <h2>Isolation check</h2>
        <p><Status tone="neutral">Not reported by the API yet</Status></p>
        <p className="small muted">The nightly cross client read test is not wired to this console. {stuck.data?.rows.length ? `${stuck.data.rows.length} runs are waiting longer than expected. See Runs.` : ""}</p>
      </section>
    </>
  );
}
