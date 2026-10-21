"use client";
// A research run as the skill's eight steps (done, running, waiting), work counts so far, files
// as they appear, Stop, and the checkpoint card when the run waits for approval. Progress comes
// from the run's event stream with polling as the fallback; the run record is polled while the
// run is open. Continue 5 times and Auto continue are kept by this page: each later pause is
// approved without asking while it stays open.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { m } from "@/lib/messages";
import { humanize } from "@/lib/ws-format";
import { cellText } from "@/lib/render";
import type { RunProgress, RunStatusView, ExportPackages } from "@/lib/gen/api.gen";
import { Notice } from "@/ui/primitives";
import { TokenMeter } from "./TokenMeter";

/** The workflow's stages grouped into the skill's eight steps, in order. */
const STEP_STAGES: string[][] = [
  ["setup"],
  ["pass1_relationship", "baseline"],
  ["news", "jobs", "tech"],
  ["people", "name_searches", "verification"],
  ["distill_grade"],
  ["build"],
  ["gate"],
  ["deliver"],
];
const STAGE_ORDER = STEP_STAGES.flat();

export type StepState = "done" | "running" | "waiting" | "paused";

export function stepStates(progress: RunProgress | null, status: string): StepState[] {
  const paused = status === "AWAITING_APPROVAL";
  const finished = status === "COMPLETED" || status === "PARTIAL";
  if (!progress) return STEP_STAGES.map((_, i) => (finished ? "done" : i === 0 ? (paused ? "paused" : "running") : "waiting"));
  const total = Math.max(progress.stages_total, 1);
  // stages done maps onto the stage order when the counts match; otherwise scale
  const doneStages = total === STAGE_ORDER.length ? progress.stages_done : Math.round((progress.stages_done / total) * STAGE_ORDER.length);
  const current = progress.current_stage ? STAGE_ORDER.findIndex((id) => id === progress.current_stage || humanize(id) === progress.current_stage) : -1;
  return STEP_STAGES.map((stages, i) => {
    const first = STAGE_ORDER.indexOf(stages[0]!);
    const last = first + stages.length - 1;
    if (finished || progress.finished || last < doneStages) return "done";
    const running = (current >= first && current <= last) || (current < 0 && first <= doneStages && doneStages <= last);
    if (running) return paused ? "paused" : "running";
    return i === 0 && doneStages === 0 && current < 0 ? (paused ? "paused" : "running") : "waiting";
  });
}

interface CapProposal {
  run_id: string; version: number; proposal_hash: string; state: string; cap_micros: number; is_extension: boolean;
  has_exclusions: boolean; has_unknown_costs: boolean; calls_by_category: Record<string, number>;
}
interface LiveSpend { status: string; as_of: string; calls_by_category: Record<string, number> }

export const TERMINAL = new Set(["COMPLETED", "PARTIAL", "BUDGET_EXCEEDED", "CANCELED", "FAILED"]);

export function useRun(runId: string | null) {
  const [run, setRun] = useState<RunStatusView | null>(null);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const status = run?.status.toUpperCase() ?? "";
  const terminal = TERMINAL.has(status);

  useEffect(() => {
    if (!runId) return;
    let alive = true;
    api<RunStatusView>("GET", `/v1/runs/${encodeURIComponent(runId)}`)
      .then((r) => { if (alive) { setRun(r); setError(null); } })
      .catch((e: unknown) => { if (alive) setError(e instanceof ApiError ? e : new ApiError(0, "network", "")); });
    return () => { alive = false; };
  }, [runId, tick]);

  // poll the run record while it is open, and follow progress by event stream or polling
  useEffect(() => {
    if (!runId || terminal) return;
    const poll = setInterval(reload, 6000);
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    const read = async () => {
      if (closed) return;
      try {
        const p = await api<RunProgress>("GET", `/v1/runs/${encodeURIComponent(runId)}/progress`);
        setProgress(p);
        if (p.finished) reload();
      } catch { /* the next poll tries again */ }
      if (!closed) timer = setTimeout(() => void read(), 5000);
    };
    if (typeof EventSource !== "undefined") {
      source = new EventSource(`/v1/runs/${encodeURIComponent(runId)}/events`);
      source.addEventListener("progress", (event) => {
        try { const p = JSON.parse((event as MessageEvent<string>).data) as RunProgress; setProgress(p); if (p.finished) reload(); } catch { /* ignore */ }
      });
      source.addEventListener("reconnect", () => { source?.close(); source = null; void read(); });
      source.onerror = () => { source?.close(); source = null; void read(); };
    } else {
      void read();
    }
    return () => { closed = true; clearInterval(poll); source?.close(); if (timer) clearTimeout(timer); };
  }, [runId, terminal, reload]);

  return { run, progress, error, status, terminal, reload };
}

function Counts({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, n]) => n > 0);
  if (!entries.length) return <p className="muted small">No work recorded yet.</p>;
  return <dl className="dl">{entries.map(([k, n]) => <div key={k} className="contents"><dt>{humanize(k)}</dt><dd className="num">{n}</dd></div>)}</dl>;
}

export function Checkpoint({ runId, status, onDecided, auto, setAuto }: { runId: string; status: string; onDecided: () => void; auto: number; setAuto: (n: number) => void }) {
  const [cap, setCap] = useState<CapProposal | null>(null);
  const [live, setLive] = useState<LiveSpend | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const paused = status === "AWAITING_APPROVAL";

  useEffect(() => {
    if (!paused) return; // the panel shows only while the run waits for approval
    let alive = true;
    api<CapProposal>("GET", `/v1/runs/${encodeURIComponent(runId)}/cap`).then((c) => { if (alive) setCap(c); }).catch(() => { if (alive) setCap(null); });
    api<LiveSpend>("GET", `/v1/runs/${encodeURIComponent(runId)}/spend/live`).then((l) => { if (alive) setLive(l); }).catch(() => undefined);
    return () => { alive = false; };
  }, [runId, paused]);

  const approve = useCallback(async () => {
    setBusy(true);
    setMessage(null);
    try {
      if (cap && (cap.state === "proposed" || cap.state === "pending")) {
        await api("POST", `/v1/runs/${encodeURIComponent(runId)}/cap/approve`, { version: cap.version, proposal_hash: cap.proposal_hash });
      } else {
        await api("POST", `/v1/runs/${encodeURIComponent(runId)}/approve`);
      }
      onDecided();
    } catch (e) {
      setMessage(e instanceof ApiError && e.status === 409 ? "The proposal changed. Reload before deciding." : "The decision was not saved. Try again.");
    } finally {
      setBusy(false);
    }
  }, [cap, runId, onDecided]);

  // Continue 5 times and Auto continue: approve later pauses while this page is open.
  const firing = useRef(false);
  useEffect(() => {
    if (!paused || busy || auto === 0 || firing.current) return;
    firing.current = true;
    if (auto > 0) setAuto(auto - 1);
    void approve().finally(() => { firing.current = false; });
  }, [paused, busy, auto, setAuto, approve]);

  const stop = async () => {
    setBusy(true);
    try { await api("POST", `/v1/runs/${encodeURIComponent(runId)}/cancel`); onDecided(); } catch { setMessage("The run could not be stopped. Try again."); } finally { setBusy(false); }
  };

  if (!paused) return null;
  return (
    <div className="notice" role="status" aria-live="polite">
      <h2>{m.run.checkpointTitle}</h2>
      <p className="small muted">{m.run.checkpointDone}</p>
      <Counts counts={live?.calls_by_category ?? cap?.calls_by_category ?? {}} />
      {cap ? <p className="small muted">{cap.is_extension ? "The run asks to continue past the approved amount." : "The run asks for approval before it continues."}{cap.has_unknown_costs ? " Some work has no known price yet." : ""}</p> : null}
      {auto !== 0 ? <p className="small">{m.run.autoOn}</p> : null}
      {message ? <p className="error-text small" role="alert">{message}</p> : null}
      <div className="actions">
        <button type="button" className="btn primary" disabled={busy} onClick={() => { setAuto(0); void approve(); }}>{m.run.continueOne}</button>
        <button type="button" className="btn" disabled={busy} onClick={() => { setAuto(4); void approve(); }}>{m.run.continueFive}</button>
        <button type="button" className="btn" disabled={busy} onClick={() => { setAuto(-1); void approve(); }}>{m.run.auto}</button>
        <button type="button" className="btn danger" disabled={busy} onClick={() => void stop()}>{m.run.stop}</button>
      </div>
    </div>
  );
}

export function StepList({ progress, status }: { progress: RunProgress | null; status: string }) {
  const states = stepStates(progress, status);
  const label: Record<StepState, string> = { done: m.run.done, running: m.run.running, waiting: m.run.waiting, paused: m.run.paused };
  return (
    <ol className="steps" aria-label="Steps">
      {m.run.steps.map((name, i) => (
        <li key={name} data-state={states[i]}>
          <span className="n">{i + 1}</span>
          <span className="grow">{name}</span>
          <span className="state">{states[i] === "running" ? <><span className="spinner" aria-hidden="true" /> </> : null}{label[states[i]!]}</span>
        </li>
      ))}
    </ol>
  );
}

export function RunFiles({ domain, runId }: { domain: string | null; runId: string }) {
  const [files, setFiles] = useState<{ name: string; href: string; size: number }[]>([]);
  useEffect(() => {
    if (!domain) return;
    let alive = true;
    api<ExportPackages>("GET", `/v1/exports?domain=${encodeURIComponent(domain)}`).then((r) => {
      if (!alive) return;
      const list = r.packages.filter((p) => p.run_id === runId && p.status === "delivered")
        .flatMap((p) => p.files.map((f) => ({ name: f.name, size: f.size, href: `/v1/exports/${encodeURIComponent(p.package_id)}/files/${f.index}` })));
      setFiles(list);
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [domain, runId]);
  if (!files.length) return <p className="muted small">{m.run.noFiles}</p>;
  return (
    <ul className="rows">
      {files.map((f) => <li key={f.href}><a href={f.href}>{cellText(f.name)}</a><span className="end">{Math.max(1, Math.round(f.size / 1024))} KB</span></li>)}
    </ul>
  );
}

export function RunPanel({ runId, compact = false }: { runId: string; compact?: boolean }) {
  const { run, progress, error, status, terminal, reload } = useRun(runId);
  const [auto, setAuto] = useState(0);
  const [stopping, setStopping] = useState(false);
  const [live, setLive] = useState<LiveSpend | null>(null);
  useEffect(() => {
    if (terminal && !run) return;
    let alive = true;
    api<LiveSpend>("GET", `/v1/runs/${encodeURIComponent(runId)}/spend/live`).then((l) => { if (alive) setLive(l); }).catch(() => undefined);
    return () => { alive = false; };
  }, [runId, status, terminal, run]);
  if (error) return <Notice kind="error">{error.status === 404 ? m.run.notFound : `${m.state.errorTitle} ${m.state.errorBody}`}</Notice>;
  const stop = async () => {
    setStopping(true);
    try { await api("POST", `/v1/runs/${encodeURIComponent(runId)}/cancel`); reload(); } catch { /* shown by the next reload */ } finally { setStopping(false); }
  };
  return (
    <div className="stack">
      <StepList progress={progress} status={status} />
      <Checkpoint runId={runId} status={status} onDecided={reload} auto={auto} setAuto={setAuto} />
      {run ? <TokenMeter tokens={run.tokens_used ?? 0} running={!terminal} /> : null}
      {!compact ? (
        <div className="section">
          <h3>{m.run.unitsSoFar}</h3>
          <Counts counts={live?.calls_by_category ?? {}} />
        </div>
      ) : null}
      {!terminal && run ? (
        <div className="actions">
          <button type="button" className="btn danger" disabled={stopping} onClick={() => void stop()}>{m.run.stop}</button>
          {compact ? <Link className="link-btn small" href={`/runs/${encodeURIComponent(runId)}`}>{m.ask.openRun}</Link> : null}
        </div>
      ) : null}
      {terminal && run ? (
        <p className="small" role="status">
          {status === "COMPLETED" ? <span className="status ok">{m.run.completed}</span>
            : status === "FAILED" ? <span className="status bad">{m.run.failed}{run.reason ? ` ${humanize(run.reason)}.` : ""}</span>
            : status === "CANCELED" ? <span className="status neutral">{m.run.stopped}</span>
            : <span className="status warn">{m.run.partial}</span>}
        </p>
      ) : null}
    </div>
  );
}
