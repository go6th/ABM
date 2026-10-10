"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { RunProgress } from "@/components/Progress";
import { SignedIn } from "@/components/SignedIn";
import { ApiError, api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { Me, RunStatusView } from "@/lib/gen/api.gen";

const STATE_LABELS: Record<string, string> = {
  QUEUED: "Queued",
  RUNNING: "Running",
  DISTILLING: "Distilling",
  VERIFYING: "Verifying",
  AWAITING_APPROVAL: "Waiting for approval",
  COMPLETED: "Completed",
  PARTIAL: "Completed with partial results",
  BUDGET_EXCEEDED: "Stopped at the approved limit",
  CANCELED: "Canceled",
  FAILED: "Failed",
};

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "BUDGET_EXCEEDED", "CANCELED", "FAILED"]);

function RunDetails({ runId, me }: { runId: string; me: Me }) {
  const path = `/v1/runs/${encodeURIComponent(runId)}`;
  const run = useApi<RunStatusView>(path);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const canApprove = me.capabilities.includes("runs:approve");
  const canCancel = me.capabilities.includes("runs:cancel");
  const status = run.data?.status.toUpperCase() ?? "";
  const isTerminal = TERMINAL.has(status);

  async function transition(action: "approve" | "cancel") {
    setBusy(true);
    setMessage(null);
    try {
      await api("POST", `${path}/${action}`);
      setMessage(action === "approve" ? "Run approved and resumed." : "Run canceled.");
      run.reload();
    } catch (error) {
      setMessage(error instanceof ApiError ? `Action failed (${error.type}). Reload the run before trying again.` : "Action failed. Reload the run before trying again.");
      run.reload();
    } finally {
      setBusy(false);
    }
  }

  if (run.error) {
    return (
      <section className="client-data-state error-box" role="alert">
        <h1>Run could not be loaded</h1>
        <p>{run.error.status === 404 ? "This run was not found in your workspace." : "The run service could not be reached."}</p>
        <button className="btn" type="button" onClick={run.reload}>Try again</button>
      </section>
    );
  }
  if (!run.data) return <p className="client-loading" role="status">Loading run…</p>;

  const coverageRows = run.data.coverage.rows;
  return (
    <div className="client-dashboard">
      <section className="client-dashboard-heading" aria-labelledby="run-title">
        <div>
          <p className="eyebrow"><Link href="/">ACCOUNT INTELLIGENCE</Link></p>
          <h1 id="run-title">Research run</h1>
          <p className="secondary">Run ID: <code>{run.data.run_id}</code></p>
        </div>
        <span className={`badge ${isTerminal ? "tone-slate" : "tone-sky"}`} aria-live="polite">
          {STATE_LABELS[status] ?? status.toLowerCase().replaceAll("_", " ")}
        </span>
      </section>

      {!isTerminal ? (
        <section className="client-accounts-panel" aria-labelledby="progress-title">
          <h2 id="progress-title">Progress</h2>
          <RunProgress runId={runId} onFinished={run.reload} />
        </section>
      ) : null}

      {run.data.reason ? (
        <section className="client-accounts-panel" aria-labelledby="outcome-title">
          <h2 id="outcome-title">Run outcome</h2>
          <p>{run.data.reason}</p>
        </section>
      ) : null}

      {status === "AWAITING_APPROVAL" && canApprove ? (
        <section className="client-accounts-panel" aria-labelledby="approval-title">
          <h2 id="approval-title">Approval required</h2>
          <p>This run is paused. Review the coverage below before allowing it to continue.</p>
          <button className="btn primary" type="button" disabled={busy} onClick={() => {
            if (window.confirm("Approve and resume this research run?")) void transition("approve");
          }}>
            {busy ? "Working…" : "Approve and resume"}
          </button>
        </section>
      ) : null}

      {!isTerminal && canCancel ? (
        <section className="client-accounts-panel" aria-labelledby="cancel-title">
          <h2 id="cancel-title">Cancel run</h2>
          <p>Canceling stops further work and keeps any results already recorded.</p>
          <button className="btn" type="button" disabled={busy} onClick={() => {
            if (window.confirm("Cancel this research run?")) void transition("cancel");
          }}>{busy ? "Working…" : "Cancel run"}</button>
        </section>
      ) : null}
      {message ? <p role="status">{message}</p> : null}

      {isTerminal ? (
        <section className="client-accounts-panel" aria-labelledby="results-title">
          <h2 id="results-title">Results</h2>
          <p className="secondary">Coverage is shown even when a run stopped early, so incomplete work remains visible.</p>
          {coverageRows.length === 0 ? <p>No coverage rows were recorded for this run.</p> : (
            <div className="client-table-wrap">
              <table className="client-account-table">
                <thead><tr><th scope="col">Research area</th><th scope="col">Status</th><th scope="col">Priority</th><th scope="col">Evidence summary</th></tr></thead>
                <tbody>{coverageRows.map((row) => (
                  <tr key={row.row}>
                    <th scope="row">{row.row}</th>
                    <td>{row.status}</td>
                    <td>{row.priority}</td>
                    <td>{row.reason ?? (row.results === null ? "No proof recorded" : `${row.results} result${row.results === 1 ? "" : "s"}`)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}

export default function RunPage() {
  const params = useParams<{ run_id: string }>();
  const runId = params.run_id;
  const me = useApi<Me>("/v1/me");
  return <SignedIn>{() => me.data
    ? <RunDetails runId={runId} me={me.data} />
    : me.error
      ? <div role="alert"><p>Access details could not be loaded.</p><button type="button" onClick={me.reload}>Try again</button></div>
      : <p role="status">Loading access details…</p>}</SignedIn>;
}
