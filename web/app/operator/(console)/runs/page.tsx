"use client";
// Runs: read-only. Runs waiting longer than expected, failures by reason, and the live spend
// of one run by id. Nothing here can limit or approve.
import { useState } from "react";
import { useOperatorApi } from "../useOperatorApi";
import { Loading, Notice, PageHead, Status } from "@/ui/primitives";
import { humanize } from "@/lib/ws-format";
import { cellText } from "@/lib/render";

function money(micros: number | null): string {
  return micros === null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(micros / 1_000_000);
}

export default function Runs() {
  const stuck = useOperatorApi("GET /operator/v1/ops/stuck");
  const failures = useOperatorApi("GET /operator/v1/ops/failures");
  const [runId, setRunId] = useState("");
  const [lookup, setLookup] = useState<string | null>(null);
  const spend = useOperatorApi("GET /operator/v1/runs/{run_id}/spend/live", { params: { run_id: lookup ?? "" } }, lookup !== null);
  const timeline = useOperatorApi("GET /operator/v1/runs/{run_id}/timeline", { params: { run_id: lookup ?? "" } }, lookup !== null);
  return (
    <>
      <PageHead title="Runs" sub="Every run across clients, read only. Nothing here can limit or approve." />
      <Notice>A list of every run needs an operator runs endpoint. Until then this page shows waiting runs, failures by reason and one run by id.</Notice>
      <section className="section">
        <h2>Waiting longer than expected</h2>
        {stuck.error ? <p className="error-text small">{stuck.error.detail}</p> : !stuck.data ? <Loading /> : stuck.data.rows.length === 0 ? <p className="muted">None.</p> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Run</th><th scope="col">State</th><th scope="col">Since</th><th scope="col">Reason</th></tr></thead>
              <tbody>{stuck.data.rows.map((r) => (
                <tr key={r.run_id}><td><button type="button" className="link-btn mono small" onClick={() => { setRunId(r.run_id); setLookup(r.run_id); }}>{r.run_id}</button></td><td><Status tone="warn">{humanize(r.state)}</Status></td><td>{r.age_bucket ?? r.at}</td><td>{r.reason ? humanize(r.reason) : "—"}</td></tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
      <section className="section">
        <h2>Failures by reason</h2>
        {failures.error ? <p className="error-text small">{failures.error.detail}</p> : !failures.data ? <Loading /> : failures.data.rows.length === 0 ? <p className="muted">None.</p> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Status</th><th scope="col">Reason</th><th scope="col" className="num">Runs</th></tr></thead>
              <tbody>{failures.data.rows.map((r, i) => <tr key={i}><td>{humanize(r.status)}</td><td>{humanize(r.reason)}</td><td className="num">{r.runs}</td></tr>)}</tbody>
            </table>
          </div>
        )}
      </section>
      <section className="section">
        <h2>One run</h2>
        <form className="form-row" onSubmit={(e) => { e.preventDefault(); if (runId.trim()) setLookup(runId.trim()); }}>
          <div className="field"><label htmlFor="run-id">Run id</label><input id="run-id" className="input mono" value={runId} onChange={(e) => setRunId(e.target.value)} maxLength={64} /></div>
          <button type="submit" className="btn">Look up</button>
        </form>
        {lookup ? (
          spend.error || timeline.error ? <p className="error-text small">{(spend.error ?? timeline.error)?.detail}</p> : !spend.data || !timeline.data ? <Loading /> : (
            <dl className="dl">
              <div className="contents"><dt>Status</dt><dd>{humanize(timeline.data.status)}</dd></div>
              <div className="contents"><dt>Started</dt><dd>{timeline.data.created_at}</dd></div>
              <div className="contents"><dt>Finished</dt><dd>{timeline.data.finished_at ?? "—"}</dd></div>
              <div className="contents"><dt>Tool spend committed</dt><dd>{money(spend.data.committed_micros)}</dd></div>
              <div className="contents"><dt>Held</dt><dd>{money(spend.data.held_micros)}</dd></div>
              <div className="contents"><dt>Approved amount</dt><dd>{money(spend.data.cap_micros)}</dd></div>
              <div className="contents"><dt>Pauses</dt><dd>{spend.data.cap_version ?? 0}</dd></div>
              <div className="contents"><dt>Spend by purpose</dt><dd>{spend.data.by_provider.length ? spend.data.by_provider.map((p, i) => <span key={i} className="small">{cellText(JSON.stringify(p), 120)} </span>) : "—"}</dd></div>
            </dl>
          )
        ) : null}
      </section>
    </>
  );
}
