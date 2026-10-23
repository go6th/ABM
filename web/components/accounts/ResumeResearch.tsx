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

/** What a resumed research says: how much it already holds and that the user will be told. */
export function ResumingNote({ resumed, compact = false }: { resumed: ResearchResumed; compact?: boolean }) {
  const text = `${resumingLine(resumed.steps_done, resumed.steps_total)}. You will be notified when it finishes.`;
  if (compact) return <span className="sm-text" role="status">{text}</span>;
  return <div className="banner" role="status"><span className="pdot" aria-hidden="true" />{text}</div>;
}

/** "Resume research" for one run the server offers it on (``can_resume``, or the account's
 * ``stopped_run``): continues it with what it holds; a retried click is the same request. Used on
 * the account page, the Research board and the run page. */
export function ResumeButton({ runId, compact = false, onResumed }: {
  runId: string;
  compact?: boolean;
  onResumed?: (resumed: ResearchResumed) => void;
}) {
  const router = useRouter();
  const requestId = useRef(crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<ResearchResumed | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const resume = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const resumed = await api<ResearchResumed>("POST", "/v1/research/resume", { run_id: runId, request_id: requestId.current });
      setDone(resumed);
      onResumed?.(resumed);
      router.refresh();
    } catch (cause) {
      requestId.current = crypto.randomUUID();
      setMessage(cause instanceof ApiError ? cause.detail : "The research could not be resumed. Try again in a moment.");
      setBusy(false);
    }
  };

  if (done) return <ResumingNote resumed={done} compact={compact} />;
  return (
    <>
      {message ? <span className="sm-text bad-text" role="alert">{message}</span> : null}
      <button type="button" className={`btn primary${compact ? " sm" : " align-start"}`} disabled={busy} onClick={() => void resume()}>{busy ? "Resuming…" : "Resume research"}</button>
    </>
  );
}

export function ResumeResearch({ stopped }: { stopped: StoppedRun }) {
  const [done, setDone] = useState<ResearchResumed | null>(null);
  if (done) return <ResumingNote resumed={done} />;
  return (
    <section className="card pad stack-10" aria-labelledby="stopped-title">
      <div className="strong" id="stopped-title">{stopped.stalled ? "This research stalled" : "This research stopped before finishing"}</div>
      <p className="mut sm-text">
        {stopped.reason} Everything found so far is kept{stopped.steps_total ? ` (${stopped.steps_done} of ${stopped.steps_total} steps done)` : ""}. Resuming reuses it at no cost.
      </p>
      <ResumeButton runId={stopped.run_id} onResumed={setDone} />
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
