"use client";
// Run: the skill's eight steps with done, running or waiting, work so far, files as they appear,
// Stop, and the checkpoint card when the run pauses. A finished run shows its coverage.
import Link from "next/link";
import { useParams } from "next/navigation";
import { RunFiles, RunPanel, useRun } from "@/components/ws/RunSteps";
import { PageHead, Status } from "@/ui/primitives";
import { m } from "@/lib/messages";
import { humanize } from "@/lib/ws-format";
import { cellText } from "@/lib/render";

export default function RunPage() {
  const params = useParams<{ run_id: string }>();
  const runId = params.run_id;
  const { run, status, terminal } = useRun(runId);
  const label = run?.input_mode === "file" ? m.account.fromFiles : null;
  return (
    <div className="page narrow">
      <PageHead
        crumb={run?.domain ? <Link href={`/accounts/${encodeURIComponent(run.domain)}`}>{cellText(run.domain)}</Link> : <Link href="/accounts">{m.run.back}</Link>}
        title={m.run.title}
        sub={label}
        actions={run?.domain && terminal ? <Link className="btn" href={`/accounts/${encodeURIComponent(run.domain)}`}>{m.run.openAccount}</Link> : null}
      />
      <RunPanel runId={runId} />
      <section className="section">
        <h2>{m.run.files}</h2>
        <RunFiles domain={run?.domain ?? null} runId={runId} />
      </section>
      {run?.gate ? (
        <section className="section">
          <h2>{m.run.gate}</h2>
          <p>{run.gate.verdict.toLowerCase() === "pass" || run.gate.verdict.toLowerCase() === "passed" ? <Status tone="ok">{m.run.gatePassed}</Status> : <Status tone="bad">{m.run.gateFailed}</Status>}</p>
          {run.gate.defects.length ? <ul className="rows">{run.gate.defects.map((d, i) => <li key={i}><span>{humanize(d.check)}</span><span className="end">{cellText(d.item, 80)}</span></li>)}</ul> : null}
        </section>
      ) : null}
      {terminal && run && run.coverage.rows.length ? (
        <section className="section">
          <h2>{m.run.coverage}</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">{m.run.coverageRow}</th><th scope="col">{m.run.status}</th><th scope="col">{m.run.reason}</th></tr></thead>
              <tbody>
                {run.coverage.rows.map((row) => (
                  <tr key={row.row}>
                    <td>{humanize(row.row)}</td>
                    <td><Status tone={row.status.toLowerCase() === "done" ? "ok" : row.status.toLowerCase() === "blocked" ? "bad" : "warn"}>{humanize(row.status)}</Status></td>
                    <td>{row.reason ? humanize(row.reason) : row.results === null ? "No proof recorded" : `${row.results} result${row.results === 1 ? "" : "s"}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      {status === "FAILED" && run?.stop_detail ? <p className="small muted">{cellText(run.stop_detail, 300)}</p> : null}
    </div>
  );
}
