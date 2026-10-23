"use client";
// The research running on this account, live: where it is ("Step 3 of 8: Reading and grading"),
// every step in a drop-down list (done, running, waiting, where it stopped), the tokens used so
// far, and Stop, Refresh and Run details. A research that has not started yet (waiting or queued)
// can be stopped too; its credits go back to the pool. When the run ends the page reloads, so the
// finished research, its gaps and its files show.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { m } from "@/lib/messages";
import type { OpenJob } from "@/lib/gen/api.gen";
import { Checkpoint, currentStep, runSteps, StepList, StopButton, useRun } from "@/components/ws/RunSteps";
import { TokenMeter } from "@/components/ws/TokenMeter";
import { LiveRefresh } from "./ResumeResearch";

export function ResearchProgress({ job, scopeName }: { job: OpenJob; scopeName: string }) {
  const router = useRouter();
  const live = useRun(job.run_id ?? null);
  const { run, progress, status, terminal, reload } = live;
  const [auto, setAuto] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);

  // the run ended (or was stopped): reload the page once to show the finished research
  const ended = useRef(false);
  useEffect(() => {
    if (!terminal || ended.current) return;
    ended.current = true;
    router.refresh();
  }, [terminal, router]);

  const stop = async () => {
    setMessage(null);
    try {
      await api("POST", `/v1/research/${encodeURIComponent(job.id)}/stop`);
      setStopped(true);
      reload();
      router.refresh();
    } catch (cause) {
      setMessage(cause instanceof ApiError && cause.status === 409 ? cause.detail : m.run.stopFailed);
    }
  };
  const refresh = () => { reload(); router.refresh(); };

  const waiting = !job.run_id;
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
        <>
          <p className="mut sm-text">{m.live.notStarted}</p>
          <LiveRefresh every={10000} />
        </>
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
        <button type="button" className="btn" onClick={refresh}>{m.run.refresh}</button>
        <Link className="link-btn small" href={`/research?r=${encodeURIComponent(job.id)}`} prefetch={false}>{m.live.watch}</Link>
        {job.run_id ? <Link className="link-btn small" href={`/runs/${encodeURIComponent(job.run_id)}`} prefetch={false}>{m.live.details}</Link> : null}
      </div>
      <p className="fine">{m.live.notified}</p>
    </section>
  );
}
