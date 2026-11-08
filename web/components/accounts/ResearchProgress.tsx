"use client";
// The research running on this account, live: where it is ("Step 3 of 8: Reading and grading"),
// every step in a drop-down list (done, running, waiting, where it stopped), the tokens used so
// far, and Stop and a link to the run. A research that has not started yet (waiting or queued)
// can be stopped too; its credits go back to the pool. The card updates itself, so it has no
// Refresh button, and it re-draws the page (the counts and the summary) only when something the
// page shows has changed: a step finished (at most every 30 seconds), the research started, or
// it ended. A queued research is checked with one small read (every 10 s, every 60 s while it
// waits for credit); a hidden tab reads nothing.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, errorText, ApiError } from "@/lib/api";
import { m } from "@/lib/messages";
import { backoff, isGone } from "@/lib/useRunStream";
import { useVisible } from "@/lib/useVisible";
import type { OpenJob, ResearchLive } from "@/lib/gen/api.gen";
import { Checkpoint, currentStep, runSteps, StepList, StopButton, useRun } from "@/components/ws/RunSteps";
import { TokenMeter } from "@/components/ws/TokenMeter";
import { announce } from "@/ui/Toaster";

export const QUEUED_CHECK_MS = 10_000;
export const CREDIT_CHECK_MS = 60_000;
export const STEP_REFRESH_GAP_MS = 30_000;

/** A research that has no run yet: one light read of the live list tells when it started (it has a
 *  run), changed state or ended, and only then is the page drawn again. */
function useStartWatch(job: OpenJob, active: boolean): void {
  const router = useRouter();
  const visible = useVisible();
  const wasHidden = useRef(false);
  // what the page was drawn with; the first read fills in the state (the list's own wording)
  const seen = useRef<{ runId: string | null; status: string | null }>({ runId: job.run_id ?? null, status: null });
  const every = job.status === "waiting_credit_limit" ? CREDIT_CHECK_MS : QUEUED_CHECK_MS;
  useEffect(() => {
    if (!active) return;
    if (!visible) { wasHidden.current = true; return; }
    const immediate = wasHidden.current;
    wasHidden.current = false;
    const controller = new AbortController();
    let closed = false;
    let fails = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      let wait = every;
      try {
        const live = await api<ResearchLive>("GET", "/v1/research/live?selected=", undefined, {}, controller.signal);
        if (closed) return;
        fails = 0;
        const item = [...live.running, ...live.recent].find((i) => i.job_id === job.id);
        const now = { runId: item?.run_id ?? null, status: item?.status ?? "gone" };
        const before = seen.current;
        seen.current = now;
        const changed = now.runId !== before.runId || (before.status !== null ? now.status !== before.status : now.status === "gone");
        if (changed) router.refresh();
      } catch (cause) {
        if (closed || isGone(cause)) return;
        fails += 1;
        wait = backoff(every, fails);
      }
      if (!closed) timer = setTimeout(() => void tick(), wait);
    };
    timer = setTimeout(() => void tick(), immediate ? 0 : every);
    return () => { closed = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [active, visible, every, job.id, router]);
}

export function ResearchProgress({ job, scopeName }: { job: OpenJob; scopeName: string }) {
  const router = useRouter();
  const visible = useVisible();
  const live = useRun(job.run_id ?? null);
  const { run, progress, status, terminal, reload } = live;
  const [auto, setAuto] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const waiting = !job.run_id;
  useStartWatch(job, waiting && !stopped);

  // the run ended (or was stopped): reload the page once to show the finished research
  const ended = useRef(false);
  useEffect(() => {
    if (!terminal || ended.current) return;
    ended.current = true;
    router.refresh();
  }, [terminal, router]);

  // a step finished: the counts and the summary on the page are older than the run, so the page
  // is drawn again, at most every STEP_REFRESH_GAP_MS and only while the tab is on screen
  const stagesDone = progress?.stages_done ?? null;
  const seenDone = useRef<number | null>(null);
  const dirty = useRef(false);
  const lastDrawn = useRef(0);
  useEffect(() => {
    if (stagesDone === null) return;
    if (seenDone.current !== null && seenDone.current !== stagesDone) dirty.current = true;
    seenDone.current = stagesDone;
    if (!dirty.current || !visible || terminal) return;
    const wait = Math.max(0, STEP_REFRESH_GAP_MS - (Date.now() - lastDrawn.current));
    const timer = setTimeout(() => {
      dirty.current = false;
      lastDrawn.current = Date.now();
      router.refresh();
    }, wait);
    return () => clearTimeout(timer);
  }, [stagesDone, visible, terminal, router]);

  const stop = async () => {
    setMessage(null);
    try {
      await api("POST", `/v1/research/${encodeURIComponent(job.id)}/stop`);
      setStopped(true);
      announce("Stopping the research");
      reload();
      router.refresh();
    } catch (cause) {
      setMessage(cause instanceof ApiError && cause.status === 409 ? errorText(cause, m.run.stopFailed) : m.run.stopFailed);
    }
  };

  const at = currentStep(progress, status);
  const headline = stopped
    ? m.live.stopping
    : waiting
      ? job.status === "waiting_credit_limit" ? m.live.waitingLimit : m.live.queued
      : !progress
        ? m.live.starting
        : at
          ? m.live.stepOf(at.index + 1, at.total, at.step.name)
          : progress.label;

  return (
    <section className="card pad stack-10 research-live" aria-labelledby="research-live-title">
      <div className="row-12 wrap">
        <span className="pdot" aria-hidden="true" />
        <div className="grow stack-4">
          <div className="strong" id="research-live-title">{m.live.title(scopeName)}</div>
          <div className="mut sm-text" role="status" aria-live="polite">
            {headline}{progress && !waiting ? ` · ${m.live.stepsDone(progress.stages_done, progress.stages_total)}` : ""}
          </div>
        </div>
      </div>

      {waiting ? (
        <p className="mut sm-text">{m.live.notStarted}</p>
      ) : (
        <details className="run-steps" open>
          <summary>{m.live.stepsSummary}{progress && at ? ` · ${at.step.name}` : ""}</summary>
          <StepList progress={progress} status={status} />
        </details>
      )}

      {job.run_id ? <Checkpoint runId={job.run_id} status={status} onDecided={reload} auto={auto} setAuto={setAuto} tokens={run?.token_use ?? null} /> : null}
      {run ? <TokenMeter run={run} steps={progress ? runSteps(progress, status) : []} at={at} status={status} /> : null}

      {message ? <p className="sm-text bad-text" role="alert">{message}</p> : null}
      <div className="actions">
        {!terminal && !stopped ? <StopButton onStop={stop} label={m.live.stop} /> : null}
        {job.run_id
          ? <Link className="link-btn small" href={`/runs/${encodeURIComponent(job.run_id)}`} prefetch={false}>{m.live.details}</Link>
          : <Link className="link-btn small" href={`/research?r=${encodeURIComponent(job.id)}`} prefetch={false}>{m.live.watch}</Link>}
      </div>
    </section>
  );
}
