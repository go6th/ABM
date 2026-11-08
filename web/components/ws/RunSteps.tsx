"use client";
// A research run as its steps (done, running, paused, waiting, stopped, not reached), the tokens
// so far, files as they appear, Stop, and the checkpoint card when the run waits for approval. The
// steps come from the server (``RunProgress.steps``: the research graph's own steps); an older
// server without them falls back to the stage counts. Progress comes from the run's event stream
// with polling as the fallback; the run record and its progress are read again every few seconds
// while the run is open and the tab is on screen, and once when it has ended. One failed read keeps
// the last steps and buttons on screen with a "Reconnecting" line. Continue 5 times and Auto
// continue are kept by this page: each later pause is approved without asking while it stays open.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ResumeButton, ResumingNote } from "@/components/accounts/ResumeResearch";
import { api, ApiError, errorText } from "@/lib/api";
import { useRunStream } from "@/lib/useRunStream";
import { m } from "@/lib/messages";
import { humanize } from "@/lib/ws-format";
import { cellText } from "@/lib/render";
import type { ProgressStep, RunProgress, RunStatusView, ExportPackages, ResearchResumed, TokenUse } from "@/lib/gen/api.gen";
import { announce } from "@/ui/Toaster";
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

/** The run on screen: its record (read again while it is open), its steps and its state. A read
 *  that fails keeps what was already loaded and sets ``error``; the polling stops for good when the
 *  run is missing (404) or the session ended (401). */
export function useRun(runId: string | null) {
  const [run, setRun] = useState<RunStatusView | null>(null);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  // `tick` re-reads the run record on demand; `stepsTick` also re-reads the steps, which only a
  // first read, a manual reload and the end of the run need: while the run is open its steps
  // arrive by event stream, so a poll that read them too doubled its requests
  const [tick, setTick] = useState(0);
  const [stepsTick, setStepsTick] = useState(0);
  const reload = useCallback(() => { setTick((t) => t + 1); setStepsTick((t) => t + 1); }, []);
  const status = run?.status.toUpperCase() ?? "";
  const terminal = TERMINAL.has(status);
  const gone = error?.status === 404 || error?.status === 401;

  // one read of the run record; a failure is remembered as ``error`` and passed on, so the poller
  // can back off or stop
  const readRun = useCallback((signal?: AbortSignal): Promise<void> => {
    if (!runId) return Promise.resolve();
    return api<RunStatusView>("GET", `/v1/runs/${encodeURIComponent(runId)}`, undefined, {}, signal).then(
      (r) => {
        if (signal?.aborted) return;
        setRun(r);
        setError(null);
      },
      (e: unknown) => {
        if (signal?.aborted) return;
        setError(e instanceof ApiError ? e : new ApiError(0, "network", ""));
        throw e;
      },
    );
  }, [runId]);

  // the first read, and every manual reload
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    api<RunStatusView>("GET", `/v1/runs/${encodeURIComponent(runId)}`, undefined, {}, controller.signal)
      .then((r) => { if (!controller.signal.aborted) { setRun(r); setError(null); } })
      .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof ApiError ? e : new ApiError(0, "network", "")); });
    return () => controller.abort();
  }, [runId, tick]);

  // the steps as well: an ended run opens no stream, and a reload reads them again at once
  useEffect(() => {
    if (!runId) return;
    let alive = true;
    api<RunProgress>("GET", `/v1/runs/${encodeURIComponent(runId)}/progress`)
      .then((p) => { if (alive) setProgress(p); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [runId, stepsTick]);

  // poll the run record while it is open, and follow progress by event stream or polling
  const onProgress = useCallback((p: RunProgress) => { setProgress(p); if (p.finished) reload(); }, [reload]);
  useRunStream(runId, !terminal && !gone, onProgress, readRun);

  return { run, progress, error, status, terminal, reload };
}

function Counts({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, n]) => n > 0);
  if (!entries.length) return <p className="muted small">No work recorded yet.</p>;
  return <dl className="dl">{entries.map(([k, n]) => <div key={k} className="contents"><dt>{humanize(k)}</dt><dd className="num">{n}</dd></div>)}</dl>;
}

/** How many times its estimate a run's tokens are, when it is at or past the token guard (3x);
 *  null otherwise. The guard pauses the run there even with spend caps off (2026-10-23). */
export function overEstimate(tokens: TokenUse | null | undefined, multiple = 3): number | null {
  const expected = tokens?.expected_tokens ?? null;
  if (!tokens || !expected || expected <= 0) return null;
  const times = tokens.total / expected;
  return times >= multiple ? times : null;
}

export function Checkpoint({ runId, status, onDecided, auto, setAuto, tokens = null }: { runId: string; status: string; onDecided: () => void; auto: number; setAuto: (n: number) => void; tokens?: TokenUse | null }) {
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
  const over = overEstimate(tokens);
  if (over !== null) {
    // the token guard: one decision at a time, never continued automatically
    return (
      <div className="notice warn" role="status" aria-live="polite">
        <h2>This run is well over its estimate</h2>
        <p className="small">It has used {over.toFixed(1)}× the tokens a research like this usually takes, so it paused to ask you. Continue lets it carry on until it reaches 3× more, or stop it now and keep what it has found.</p>
        {message ? <p className="error-text small" role="alert">{message}</p> : null}
        <div className="actions">
          <button type="button" className="btn primary" disabled={busy} aria-busy={busy || undefined} onClick={() => { setAuto(0); void approve(); }}>{m.run.continueOne}</button>
          <button type="button" className="btn danger" disabled={busy} onClick={() => void stop()}>{m.run.stop}</button>
        </div>
      </div>
    );
  }
  return (
    <div className="notice" role="status" aria-live="polite">
      <h2>{m.run.checkpointTitle}</h2>
      <p className="small muted">{m.run.checkpointDone}</p>
      <Counts counts={live?.calls_by_category ?? cap?.calls_by_category ?? {}} />
      {cap ? <p className="small muted">{cap.is_extension ? "The run asks to continue past the approved amount." : "The run asks for approval before it continues."}{cap.has_unknown_costs ? " Some work has no known price yet." : ""}</p> : null}
      {auto !== 0 ? <p className="small">{m.run.autoOn}</p> : null}
      {message ? <p className="error-text small" role="alert">{message}</p> : null}
      <div className="actions">
        <button type="button" className="btn primary" disabled={busy} aria-busy={busy || undefined} onClick={() => { setAuto(0); void approve(); }}>{m.run.continueOne}</button>
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
      <button type="button" className="btn danger" disabled={busy} aria-busy={busy || undefined} onClick={() => { setBusy(true); void onStop().finally(() => { setBusy(false); setAsking(false); }); }}>{busy ? m.run.stopping : m.run.stopNow}</button>
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

/** What the panel says about a run that has ended, in the same words as the token card's pill:
 *  Completed and Partial finished, Failed, Stopped and Over budget stopped before they finished. */
function endNote(status: string): { tone: "ok" | "bad" | "neutral" | "warn"; text: string } | null {
  if (status === "COMPLETED") return { tone: "ok", text: m.run.completed };
  if (status === "FAILED") return { tone: "bad", text: `${m.run.failed} Everything found so far is kept.` };
  if (status === "CANCELED") return { tone: "neutral", text: m.run.stopped };
  if (status === "BUDGET_EXCEEDED") return { tone: "warn", text: "Stopped: it reached its limit before it finished. Everything found so far is kept." };
  if (status === "PARTIAL") return { tone: "warn", text: m.run.partial };
  return null;
}

/** The research run as the run page and the Ask side panel show it: the steps, the checkpoint
 *  card, the token card, Stop (while it is open) and how it ended. It updates itself, so there is
 *  no Refresh button; a failed read keeps the last data and says it is reconnecting. */
export function RunPanel({ runId, compact = false, state }: { runId: string; compact?: boolean; state?: ReturnType<typeof useRun> }) {
  const own = useRun(state ? null : runId);
  const { run, progress, error, status, terminal, reload } = state ?? own;
  const [auto, setAuto] = useState(0);
  const [stopError, setStopError] = useState<string | null>(null);
  // Resume research when the server offers it on this run (``can_resume``: the account page's
  // rule); the note stays after the offer goes (the account then has a job open)
  const [resumed, setResumed] = useState<ResearchResumed | null>(null);
  const onResumed = useCallback((r: ResearchResumed) => { setResumed(r); reload(); }, [reload]);
  // a run that is missing, or a session that ended, will not come back by waiting
  const gone = error?.status === 404 || error?.status === 401;
  // the error replaces the panel only when nothing has loaded: later, one failed read must not
  // take away the steps, Stop or the checkpoint buttons
  if (error && (!run || gone)) {
    return <Notice kind="error">{error.status === 404 ? m.run.notFound : error.status === 401 ? "Your session has ended. Reload the page to sign in again." : `${m.state.errorTitle} ${m.state.errorBody}`}</Notice>;
  }
  const stop = async () => {
    setStopError(null);
    try {
      await api("POST", `/v1/runs/${encodeURIComponent(runId)}/cancel`);
      announce("Research stopped");
      reload();
    } catch (e) {
      setStopError(e instanceof ApiError && e.status === 403 ? m.run.stopNotAllowed : errorText(e, m.run.stopFailed));
    }
  };
  const ended = terminal && run ? endNote(status) : null;
  return (
    <div className="stack">
      <StepList progress={progress} status={status} />
      <Checkpoint runId={runId} status={status} onDecided={reload} auto={auto} setAuto={setAuto} tokens={run?.token_use ?? null} />
      {run ? <TokenMeter run={run} steps={progress ? runSteps(progress, status) : []} at={currentStep(progress, status)} status={status} /> : null}
      {error ? <p className="mut sm-text" role="status">Reconnecting…</p> : null}
      <div className="actions">
        {!terminal && run ? <StopButton onStop={stop} /> : null}
        {compact ? <Link className="link-btn small" href={`/runs/${encodeURIComponent(runId)}`}>{m.ask.openRun}</Link> : null}
      </div>
      {stopError ? <p className="sm-text bad-text" role="alert">{stopError}</p> : null}
      {ended ? (
        <p className="small" role="status">
          <span className={`status ${ended.tone}`}>{ended.text}</span>
        </p>
      ) : null}
      {resumed ? <ResumingNote resumed={resumed} />
        : run?.can_resume ? <div className="actions"><ResumeButton runId={runId} onResumed={onResumed} /></div> : null}
    </div>
  );
}
