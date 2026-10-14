"use client";
// A research that stopped before finishing, or stalled (owner decision 2026-10-14): what it
// holds is kept; "Resume research" continues it and reuses everything already found at no cost,
// so only the missing steps are paid for. It uses none of the user's researches.
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ResearchResumed, StoppedRun } from "@/lib/gen/api.gen";

export function resumingLine(done: number, total: number): string {
  return total > 0 ? `Resuming: ${done} of ${total} steps already done` : "Resuming";
}

export function ResumeResearch({ stopped }: { stopped: StoppedRun }) {
  const router = useRouter();
  const requestId = useRef(crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<ResearchResumed | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const resume = async () => {
    setBusy(true);
    setMessage(null);
    try {
      setDone(await api<ResearchResumed>("POST", "/v1/research/resume", { run_id: stopped.run_id, request_id: requestId.current }));
      router.refresh();
    } catch (cause) {
      requestId.current = crypto.randomUUID();
      setMessage(cause instanceof ApiError ? cause.detail : "The research could not be resumed. Try again in a moment.");
      setBusy(false);
    }
  };

  if (done) {
    return <div className="banner" role="status"><span className="pdot" aria-hidden="true" />{resumingLine(done.steps_done, done.steps_total)}. You will be notified when it finishes.</div>;
  }
  return (
    <section className="card pad stack-10" aria-labelledby="stopped-title">
      <div className="strong" id="stopped-title">{stopped.stalled ? "This research stalled" : "This research stopped before finishing"}</div>
      <p className="mut sm-text">
        {stopped.reason} Everything found so far is kept{stopped.steps_total ? ` (${stopped.steps_done} of ${stopped.steps_total} steps done)` : ""}. Resuming reuses it at no cost.
      </p>
      {message ? <p className="sm-text bad-text" role="alert">{message}</p> : null}
      <button type="button" className="btn primary align-start" disabled={busy} onClick={() => void resume()}>{busy ? "Resuming…" : "Resume research"}</button>
    </section>
  );
}

/** While a research runs, the page reloads its counts every 15 seconds (live progress). */
export function LiveRefresh({ every = 15000 }: { every?: number }) {
  const router = useRouter();
  useEffect(() => {
    const timer = setInterval(() => router.refresh(), every);
    return () => clearInterval(timer);
  }, [router, every]);
  return null;
}
