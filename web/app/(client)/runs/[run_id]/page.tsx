"use client";
// One research run: its state, progress, outcome and coverage; approve or cancel; the spend cap
// decision the run is waiting on (GET /v1/runs/{id}/cap, approve or reject that exact proposal)
// and the live work counts by category while it runs (GET /v1/runs/{id}/spend/live).
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { RunProgress } from "@/ui/RunProgress";
import { SignedIn } from "@/ui/SignedIn";
import { ApiError, api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { humanize } from "@/lib/ws-format";
import { Badge, Banner, LoadError, PageHead, PageSkeleton, Section, Tile } from "@/ui/primitives";
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

interface CapProposal {
  run_id: string; version: number; proposal_hash: string; state: string; cap_micros: number; is_extension: boolean;
  has_exclusions: boolean; has_unknown_costs: boolean; calls_by_category: Record<string, number>;
}
interface LiveSpend { status: string; as_of: string; calls_by_category: Record<string, number> }

function money(micros: number): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(micros / 1_000_000);
}

/** A confirmation dialog in place of window.confirm. */
function Confirm({ trigger, title, body, action, tone = "primary", busy, onConfirm, children }: {
  trigger: string; title: string; body: string; action: string; tone?: "primary" | "danger"; busy: boolean; onConfirm: () => void; children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger className={tone === "danger" ? "btn danger" : "btn primary"} disabled={busy}>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description className="desc">{body}</Dialog.Description>
          {children}
          <div className="actions">
            <Dialog.Close className="btn quiet">Keep as is</Dialog.Close>
            <button type="button" className={tone === "danger" ? "btn danger" : "btn primary"} disabled={busy} onClick={() => { setOpen(false); onConfirm(); }}>{action}</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Counts({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, n]) => n > 0);
  if (!entries.length) return <p className="ink-2">No work recorded yet.</p>;
  return (
    <dl className="dl">
      {entries.map(([k, n]) => <div key={k} className="contents"><dt>{humanize(k)}</dt><dd className="num">{n}</dd></div>)}
    </dl>
  );
}

function CapDecision({ runId, canDecide, onDecided }: { runId: string; canDecide: boolean; onDecided: () => void }) {
  const cap = useApi<CapProposal>(`/v1/runs/${encodeURIComponent(runId)}/cap`);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  if (!cap.data) return null;
  const p = cap.data;
  const pending = p.state.toLowerCase() === "proposed" || p.state.toLowerCase() === "pending";
  const decide = async (kind: "approve" | "reject") => {
    setBusy(true);
    setMessage(null);
    try {
      await api("POST", `/v1/runs/${encodeURIComponent(runId)}/cap/${kind}`, kind === "approve"
        ? { version: p.version, proposal_hash: p.proposal_hash }
        : { version: p.version, reason: reason.trim() || "Not approved" });
      setMessage({ ok: true, text: kind === "approve" ? "Spend limit approved." : "Spend limit rejected." });
      cap.reload();
      onDecided();
    } catch (error) {
      setMessage({ ok: false, text: error instanceof ApiError && error.status === 409 ? "The proposal changed. Review the new one before deciding." : "The decision was not saved. Try again." });
      cap.reload();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Section title={p.is_extension ? "Spend limit extension" : "Spend limit"} sub="Approving lets the run spend up to this amount. It stops there.">
      <div className="card stack">
        <div className="row between">
          <span className="t-title2 num">{money(p.cap_micros)}</span>
          <Badge tone={pending ? "butter" : "mint"} dot>{humanize(p.state)}</Badge>
        </div>
        {p.has_unknown_costs ? <Banner kind="warn">Some work has no known price yet, so the final amount may be lower than the limit.</Banner> : null}
        {p.has_exclusions ? <Banner kind="info">Some sources are excluded by your workspace settings.</Banner> : null}
        <div><p className="strong">Planned work</p><Counts counts={p.calls_by_category} /></div>
        {pending && canDecide ? (
          <div className="row">
            <Confirm trigger="Approve spend limit" title="Approve this spend limit?" body={`The run may spend up to ${money(p.cap_micros)} and stops there.`} action="Approve" busy={busy} onConfirm={() => void decide("approve")} />
            <Confirm trigger="Reject" tone="danger" title="Reject this spend limit?" body="The run will not continue past this point." action="Reject" busy={busy} onConfirm={() => void decide("reject")}>
              <div className="field gap-top">
                <label htmlFor="reject-reason">Reason (optional)</label>
                <input id="reject-reason" className="input" maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} />
              </div>
            </Confirm>
          </div>
        ) : null}
        {message ? <p className={message.ok ? "inline-status" : "inline-error"} role="status">{message.text}</p> : null}
      </div>
    </Section>
  );
}

function LiveWork({ runId }: { runId: string }) {
  const live = useApi<LiveSpend>(`/v1/runs/${encodeURIComponent(runId)}/spend/live`);
  if (!live.data) return null;
  return (
    <Section title="Work so far" sub="Counts of completed work by category. Refreshes when you reload.">
      <div className="card"><Counts counts={live.data.calls_by_category} /></div>
    </Section>
  );
}

function RunDetails({ runId, me }: { runId: string; me: Me }) {
  const path = `/v1/runs/${encodeURIComponent(runId)}`;
  const run = useApi<RunStatusView>(path);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const canApprove = me.capabilities.includes("runs:approve");
  const canCancel = me.capabilities.includes("runs:cancel");
  const status = run.data?.status.toUpperCase() ?? "";
  const isTerminal = TERMINAL.has(status);
  const seesSpend = me.role === "admin" || me.role === "seller" || me.role === "viewer";

  async function transition(action: "approve" | "cancel") {
    setBusy(true);
    setMessage(null);
    try {
      await api("POST", `${path}/${action}`);
      setMessage({ ok: true, text: action === "approve" ? "Run approved and resumed." : "Run canceled." });
      run.reload();
    } catch (error) {
      setMessage({ ok: false, text: error instanceof ApiError ? `Action failed (${error.type}). Reload the run before trying again.` : "Action failed. Reload the run before trying again." });
      run.reload();
    } finally {
      setBusy(false);
    }
  }

  if (run.error) {
    return (
      <div className="content stack">
        <PageHead title="Research run" />
        {run.error.status === 404
          ? <Banner kind="error" title="Run could not be loaded">This run was not found in your workspace.</Banner>
          : <LoadError reference={null} retryHref={`/runs/${encodeURIComponent(runId)}`} />}
      </div>
    );
  }
  if (!run.data) return <PageSkeleton />;

  const coverageRows = run.data.coverage.rows;
  const done = coverageRows.filter((r) => r.status.toLowerCase() === "done").length;
  return (
    <div className="content">
      <PageHead
        eyebrow={<Link className="link" href="/runs">Research runs</Link>}
        title="Research run"
        sub={<span className="mono t-footnote">{run.data.run_id}</span>}
        actions={<span aria-live="polite"><Badge tone={isTerminal ? (status === "COMPLETED" ? "mint" : status === "FAILED" ? "rose" : "butter") : "peri"} dot>{STATE_LABELS[status] ?? humanize(status)}</Badge></span>}
      />

      {!isTerminal ? (
        <Section title="Progress" id="progress">
          <div className="card"><RunProgress runId={runId} onFinished={run.reload} /></div>
        </Section>
      ) : null}

      {run.data.reason ? (
        <Section title="Run outcome" id="outcome">
          <p>{humanize(run.data.reason)}</p>
        </Section>
      ) : null}

      {seesSpend && status === "AWAITING_APPROVAL" ? <CapDecision runId={runId} canDecide={me.role === "admin" || me.role === "seller"} onDecided={run.reload} /> : null}

      {status === "AWAITING_APPROVAL" && canApprove ? (
        <Section title="Approval required" id="approval">
          <div className="card stack">
            <p>This run is paused. Review the coverage below before allowing it to continue.</p>
            <div><Confirm trigger="Approve and resume" title="Approve and resume this research run?" body="The run continues from where it paused." action="Approve and resume" busy={busy} onConfirm={() => void transition("approve")} /></div>
          </div>
        </Section>
      ) : null}

      {seesSpend && !isTerminal ? <LiveWork runId={runId} /> : null}

      {!isTerminal && canCancel ? (
        <Section title="Cancel run" id="cancel">
          <div className="card stack">
            <p>Canceling stops further work and keeps any results already recorded.</p>
            <div><Confirm trigger="Cancel run" tone="danger" title="Cancel this research run?" body="Work stops now. Results already recorded are kept." action="Cancel run" busy={busy} onConfirm={() => void transition("cancel")} /></div>
          </div>
        </Section>
      ) : null}
      {message ? <p className={message.ok ? "inline-status" : "inline-error"} role="status">{message.text}</p> : null}

      {isTerminal ? (
        <Section title="Results" id="results" sub="Coverage is shown even when a run stopped early, so incomplete work stays visible.">
          {coverageRows.length ? (
            <div className="grid-3 block-gap">
              <Tile tone="mint" label="Research areas done" value={done} note={`of ${coverageRows.length}`} />
              <Tile tone="butter" label="Not done" value={coverageRows.length - done} />
            </div>
          ) : null}
          {coverageRows.length === 0 ? <p className="ink-2">No coverage rows were recorded for this run.</p> : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th scope="col">Research area</th><th scope="col">Status</th><th scope="col">Priority</th><th scope="col">Evidence summary</th></tr></thead>
                <tbody>{coverageRows.map((row) => (
                  <tr key={row.row}>
                    <th scope="row">{humanize(row.row)}</th>
                    <td><Badge tone={row.status.toLowerCase() === "done" ? "mint" : "butter"}>{humanize(row.status)}</Badge></td>
                    <td>{humanize(String(row.priority))}</td>
                    <td>{row.reason ? humanize(row.reason) : row.results === null ? "No proof recorded" : `${row.results} result${row.results === 1 ? "" : "s"}`}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </Section>
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
      ? <LoadError reference={null} retryHref={`/runs/${encodeURIComponent(runId)}`} />
      : <PageSkeleton />}</SignedIn>;
}
