"use client";
// What a stopped run holds and how to continue it: the plain stop reason, the stored counts, the
// research areas still missing, the collected findings on demand (GET /v1/runs/{id}/collected),
// and Resume (POST /v1/runs with resume_from), which reuses every finished pull at no cost.
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { humanize } from "@/lib/ws-format";
import { GATE_CHECKS, canResume, stopExplanation } from "@/lib/run-stop";
import { Badge, Banner, Section, Tile } from "@/ui/primitives";
import type { GateSummary, MissingRow, RunCollected, StoredCounts } from "@/lib/gen/api.gen";

export function StopReason({ reason, detail, showDetail }: { reason: string | null | undefined; detail: string | null | undefined; showDetail: boolean }) {
  const text = stopExplanation(reason);
  if (!text) return null;
  return (
    <Section title="Why the run stopped" id="outcome">
      <div className="card stack">
        <p>{text}</p>
        {showDetail && detail ? (
          <details>
            <summary className="ink-2 t-footnote">Technical detail</summary>
            <p className="mono t-footnote">{detail}</p>
          </details>
        ) : null}
      </div>
    </Section>
  );
}

export function GateDefects({ gate }: { gate: GateSummary | null | undefined }) {
  if (!gate || gate.verdict !== "fail" || !gate.defects.length) return null;
  const byCheck = new Map<string, number>();
  for (const d of gate.defects) byCheck.set(d.check, (byCheck.get(d.check) ?? 0) + 1);
  return (
    <Section title="Quality check" sub="What stopped the release. Nothing was sent to anyone.">
      <ul className="stack" aria-label="Quality check problems">
        {[...byCheck.entries()].map(([check, n]) => (
          <li key={check} className="row between">
            <span>{GATE_CHECKS[check] ?? humanize(check)}</span>
            <Badge tone="rose">{n}</Badge>
          </li>
        ))}
      </ul>
    </Section>
  );
}

export function StoredSummary({ stored }: { stored: StoredCounts }) {
  return (
    <Section title="What this run holds" sub="Kept whatever happened to the run. Counts are for this run only.">
      <div className="grid-3 block-gap">
        <Tile tone="mint" label="Sources pulled" value={stored.paid_calls} note={stored.cached_calls ? `plus ${stored.cached_calls} reused at no cost` : undefined} />
        <Tile tone="peri" label="Findings" value={stored.facts} />
        <Tile tone="butter" label="Files built" value={stored.deliverables} note={stored.drafts ? "Draft summary kept" : undefined} />
      </div>
    </Section>
  );
}

export function MissingAreas({ missing }: { missing: MissingRow[] }) {
  if (!missing.length) return null;
  return (
    <Section title="Still missing" sub="Research areas without a finished result, and why.">
      <ul className="stack" aria-label="Research areas still missing">
        {missing.map((m) => (
          <li key={m.row} className="row between">
            <span>{humanize(m.row)}</span>
            <span className="row">
              <Badge tone="butter">{humanize(m.status)}</Badge>
              <span className="ink-2 t-footnote">{m.reason ? humanize(m.reason) : "No reason recorded"}</span>
            </span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

export function CollectedView({ data }: { data: RunCollected }) {
  const claims = [...(data.drafts.narrative ?? []), ...(data.drafts.hypotheses ?? []), ...(data.drafts.questions ?? [])];
  return (
    <div className="stack">
      {data.pulls.length ? (
        <div>
          <p className="strong">Sources pulled</p>
          <dl className="dl">
            {data.pulls.map((p) => (
              <div key={p.tool_category} className="contents">
                <dt>{humanize(p.tool_category)}</dt>
                <dd className="num">{p.calls}{p.cached ? ` (${p.cached} reused)` : ""}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      {data.facts.length ? (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Finding</th><th scope="col">Value</th><th scope="col">Grade</th></tr></thead>
            <tbody>{data.facts.map((f) => (
              <tr key={f.fact_id}>
                <th scope="row">{humanize(f.attribute.split(".").slice(-1)[0] ?? f.attribute)}</th>
                <td>{f.value ?? "Not stated"}</td>
                <td>{f.grade ? humanize(f.grade) : "Not graded"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : <p className="ink-2">No findings were graded before the run stopped.</p>}
      {claims.length ? (
        <div>
          <p className="strong">Draft summary</p>
          <ul className="stack">{claims.map((c, i) => <li key={i}>{c.text}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}

function Collected({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const data = useApi<RunCollected>(open ? `/v1/runs/${encodeURIComponent(runId)}/collected` : null);
  return (
    <Section title="Collected so far" sub="Everything the run gathered, including runs that stopped early.">
      {!open ? (
        <button type="button" className="btn quiet" onClick={() => setOpen(true)}>Show collected findings</button>
      ) : data.error ? (
        <Banner kind="error">The collected findings could not be loaded. Reload the page to try again.</Banner>
      ) : data.data ? <CollectedView data={data.data} /> : <p className="ink-2">Loading…</p>}
    </Section>
  );
}

export function ResumeRun({ domain, runId }: { domain: string; runId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const inFlight = useRef(false);
  const resume = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    key.current ??= newIdempotencyKey();
    try {
      const created = await api<{ run_id: string }>("POST", "/v1/runs", { domain, resume_from: runId }, { "Idempotency-Key": key.current });
      setOpen(false);
      key.current = null;
      router.push(`/runs/${encodeURIComponent(created.run_id)}`);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 402) setError("Your workspace does not have enough credits to start a run.");
      else if (cause instanceof ApiError && cause.status === 409) setError("A run for this account is already starting. Try again in a moment.");
      else setError("The run could not be started. Try again.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return (
    <Section title="Resume this research" sub="A new run continues from here. Sources already pulled are reused at no cost; only the missing work is paid for.">
      <Dialog.Root open={open} onOpenChange={(v) => { setOpen(v); if (!v) { setError(null); key.current = null; } }}>
        <Dialog.Trigger className="btn primary" disabled={busy}>Resume research</Dialog.Trigger>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="dialog">
            <Dialog.Title>Resume this research?</Dialog.Title>
            <Dialog.Description className="desc">A new run starts for {domain}. Everything this run already pulled is reused at no cost, and only the missing work is paid for.</Dialog.Description>
            {error ? <p className="banner error t-footnote gap-top" role="alert">{error}</p> : null}
            <div className="actions">
              <Dialog.Close className="btn quiet">Not now</Dialog.Close>
              <button type="button" className="btn primary" disabled={busy} onClick={() => void resume()}>Resume research</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </Section>
  );
}

/** Everything a finished run shows about how it ended and what it kept. */
export function RunStopReport({ runId, status, reason, detail, stored, missing, domain, canStart, isAdmin, showMissing = true, gate = null, fileMode = false }: {
  runId: string; status: string; reason: string | null | undefined; detail: string | null | undefined;
  stored: StoredCounts | null | undefined; missing: MissingRow[]; domain: string | null | undefined; canStart: boolean; isAdmin: boolean;
  showMissing?: boolean; gate?: GateSummary | null; fileMode?: boolean;
}) {
  return (
    <>
      <StopReason reason={reason} detail={detail} showDetail={isAdmin} />
      <GateDefects gate={gate} />
      {stored ? <StoredSummary stored={stored} /> : null}
      {/* the page's Results table already lists every area with its status and reason; the
          short list is for pages without that table */}
      {showMissing ? <MissingAreas missing={missing} /> : null}
      {stored ? <Collected runId={runId} /> : null}
      {canStart && domain && !fileMode && canResume(status, stored) ? <ResumeRun domain={domain} runId={runId} /> : null}
    </>
  );
}
