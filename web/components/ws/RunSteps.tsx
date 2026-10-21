"use client";
// A research run as its steps (done, running, paused, waiting, stopped, not reached), work counts
// so far, files as they appear, Stop, Refresh, and the checkpoint card when the run waits for
// approval. The steps come from the server (``RunProgress.steps``: the research graph's own steps);
// an older server without them falls back to the stage counts. Progress comes from the run's event
// stream with polling as the fallback; the run record and its progress are read again every few
// seconds while the run is open, and once when it has ended. Continue 5 times and Auto continue are
// kept by this page: each later pause is approved without asking while it stays open.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { m } from "@/lib/messages";
import { humanize } from "@/lib/ws-format";
import { cellText } from "@/lib/render";
import type { ProgressStep, RunProgress, RunStatusView, ExportPackages } from "@/lib/gen/api.gen";
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

export type StepState = "done" | "running" | "waiting" | "paused" | "stopped" | "not_reached";

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
    // the steps as well: an ended run opens no stream, and Refresh reads them again at once
    api<RunProgress>("GET", `/v1/runs/${encodeURIComponent(runId)}/progress`)
      .then((p) => { if (alive) setProgress(p); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [runId, tick]);

  // poll the run record while it is open, and follow progress by event stream or polling
  useEffect(() => {
    if (!runId || terminal) return;
    const poll = setInterval(reload, 3000);
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
      // polling only while no stream is open
      if (!closed && !source) timer = setTimeout(() => void read(), 5000);
    };
    let retry: ReturnType<typeof setTimeout> | null = null;
    const open = () => {
      if (closed) return;
      source = new EventSource(`/v1/runs/${encodeURIComponent(runId)}/events`);
      source.addEventListener("progress", (event) => {
        try { const p = JSON.parse((event as MessageEvent<string>).data) as RunProgress; setProgress(p); if (p.finished) reload(); } catch { /* ignore */ }
      });
      // the server ends each stream at its duration cap; the browser reconnects on its own at
      // the server's retry (it used to fall back to polling for good after the first minute)
      source.onerror = () => {
        if (closed || !source || source.readyState === EventSource.CONNECTING) return;
        // it could not be opened: poll, and try the stream again a little later
        source.close();
        source = null;
        void read();
        retry = setTimeout(open, 10000);
      };
    };
    if (typeof EventSource !== "undefined") open();
    else void read();
    return () => { closed = true; clearInterval(poll); source?.close(); if (timer) clearTimeout(timer); if (retry) clearTimeout(retry); };
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

const STATE_LABEL: Record<StepState, string> = {
  done: m.run.done, running: m.run.running, waiting: m.run.waiting, paused: m.run.paused, stopped: m.run.stepStopped, not_reached: m.run.notReached,
};

/** The run's steps: the server's own list, or the stage counts mapped onto the eight steps. */
export function runSteps(progress: RunProgress | null, status: string): ProgressStep[] {
  if (progress?.steps?.length) return progress.steps;
  const states = stepStates(progress, status);
  return m.run.steps.map((name, i) => ({ id: String(i), name, state: states[i]! }));
}

export function StepList({ progress, status }: { progress: RunProgress | null; status: string }) {
  // the steps arrive with the first progress read: no made-up list before it
  if (!progress) return <p className="mut sm-text" role="status">{m.run.loadingSteps}</p>;
  const steps = runSteps(progress, status);
  return (
    <ol className="steps" aria-label="Steps">
      {steps.map((st, i) => {
        const state = (st.state in STATE_LABEL ? st.state : "waiting") as StepState;
        return (
          <li key={st.id} data-state={state} aria-current={state === "running" || state === "paused" ? "step" : undefined}>
            <span className="n">{i + 1}</span>
            <span className="grow">
              {st.name}
              {st.rows_total ? <span className="mut sm-text step-rows">{m.run.rowsReported(st.rows_done ?? 0, st.rows_total)}</span> : null}
            </span>
            <span className="state">{state === "running" ? <><span className="spinner" aria-hidden="true" /> </> : null}{STATE_LABEL[state]}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** The step the run is on (running, paused or where it stopped), or null when every step is done. */
export function currentStep(progress: RunProgress | null, status: string): { index: number; step: ProgressStep; total: number } | null {
  const steps = runSteps(progress, status);
  const index = steps.findIndex((st) => st.state === "running" || st.state === "paused" || st.state === "stopped" || st.state === "waiting");
  return index < 0 ? null : { index, step: steps[index]!, total: steps.length };
}

/** Stop with one confirmation: the first press asks, the second stops (results so far are kept). */
export function StopButton({ onStop, label = m.run.stop }: { onStop: () => Promise<void>; label?: string }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!asking) return <button type="button" className="btn danger" onClick={() => setAsking(true)}>{label}</button>;
  return (
    <span className="actions" role="group" aria-label={m.run.stopConfirm}>
      <span className="sm-text">{m.run.stopConfirm}</span>
      <button type="button" className="btn danger" disabled={busy} onClick={() => { setBusy(true); void onStop().finally(() => { setBusy(false); setAsking(false); }); }}>{busy ? m.run.stopping : m.run.stopNow}</button>
      <button type="button" className="btn" disabled={busy} onClick={() => setAsking(false)}>{m.run.keepRunning}</button>
    </span>
  );
}

export function RunFiles({ domain, runId, status = "" }: { domain: string | null; runId: string; status?: string }) {
  const [files, setFiles] = useState<{ name: string; href: string; size: number }[]>([]);
  // read again when the run's status moves (the files are delivered at its end)
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
  }, [domain, runId, status]);
  if (!files.length) return <p className="muted small">{m.run.noFiles}</p>;
  return (
    <ul className="rows">
      {files.map((f) => <li key={f.href}><a href={f.href}>{cellText(f.name)}</a><span className="end">{Math.max(1, Math.round(f.size / 1024))} KB</span></li>)}
    </ul>
  );
}

export function RunPanel({ runId, compact = false, state }: { runId: string; compact?: boolean; state?: ReturnType<typeof useRun> }) {
  const own = useRun(state ? null : runId);
  const { run, progress, error, status, terminal, reload } = state ?? own;
  const [auto, setAuto] = useState(0);
  const [live, setLive] = useState<LiveSpend | null>(null);
  useEffect(() => {
    if (terminal && !run) return;
    let alive = true;
    api<LiveSpend>("GET", `/v1/runs/${encodeURIComponent(runId)}/spend/live`).then((l) => { if (alive) setLive(l); }).catch(() => undefined);
    return () => { alive = false; };
  }, [runId, status, terminal, run]);
  const [stopError, setStopError] = useState<string | null>(null);
  if (error) return <Notice kind="error">{error.status === 404 ? m.run.notFound : `${m.state.errorTitle} ${m.state.errorBody}`}</Notice>;
  const stop = async () => {
    setStopError(null);
    try { await api("POST", `/v1/runs/${encodeURIComponent(runId)}/cancel`); reload(); } catch (e) {
      setStopError(e instanceof ApiError && e.status === 403 ? m.run.stopNotAllowed : m.run.stopFailed);
    }
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
      <div className="actions">
        {!terminal && run ? <StopButton onStop={stop} /> : null}
        <button type="button" className="btn" onClick={reload}>{m.run.refresh}</button>
        {compact ? <Link className="link-btn small" href={`/runs/${encodeURIComponent(runId)}`}>{m.ask.openRun}</Link> : null}
      </div>
      {stopError ? <p className="sm-text bad-text" role="alert">{stopError}</p> : null}
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
