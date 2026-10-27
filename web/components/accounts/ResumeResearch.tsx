"use client";
// A research that stopped before finishing, or stalled (owner decision 2026-10-14): what it
// holds is kept; "Resume research" continues it and reuses everything already found at no cost,
// so only the missing steps are paid for. It uses none of the user's researches.
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ResearchResumed, StoppedRun } from "@/lib/gen/api.gen";

function randomKey(): string {
  // crypto.randomUUID exists only in secure contexts (https, localhost)
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  b[6] = (b[6]! & 0x0f) | 0x40;
  b[8] = (b[8]! & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** The ``request_id`` of one thing the user starts (a research, a refresh, a resume, a download).
 * The server answers a repeated id with the first job, so the same id is sent again while the
 * body is unchanged and the last attempt may have reached the server (a network error or a 5xx):
 * a retry then returns the job already started instead of starting a second, paid one. A definite
 * refusal (4xx) or a changed body gets a new id. */
export function useRequestKey() {
  const last = useRef<{ key: string; body: string } | null>(null);
  return useMemo(() => ({
    key(body: unknown): string {
      const text = JSON.stringify(body) ?? "";
      if (!last.current || last.current.body !== text) last.current = { key: randomKey(), body: text };
      return last.current.key;
    },
    failed(cause: unknown): void {
      if (cause instanceof ApiError && cause.status < 500) last.current = null;
    },
    done(): void {
      last.current = null;
    },
  }), []);
}

/** The server's reason when it gave one, else the fallback (a bare status line has none). */
export function reasonOr(cause: unknown, fallback: string): string {
  return cause instanceof ApiError && cause.detail?.trim() ? cause.detail : fallback;
}

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
  const request = useRequestKey();
  // a second click before the first re-render must not post again
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<ResearchResumed | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const resume = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setMessage(null);
    try {
      const resumed = await api<ResearchResumed>("POST", "/v1/research/resume", { run_id: runId, request_id: request.key({ run_id: runId }) });
      request.done();
      setDone(resumed);
      onResumed?.(resumed);
      router.refresh();
    } catch (cause) {
      request.failed(cause);
      setMessage(reasonOr(cause, "The research could not be resumed. Try again in a moment."));
      setBusy(false);
    } finally {
      inFlight.current = false;
    }
  };

  if (done) return <ResumingNote resumed={done} compact={compact} />;
  return (
    <>
      {message ? <span className="sm-text bad-text" role="alert">{message}</span> : null}
      <button type="button" className={`btn primary${compact ? " sm" : " align-start"}`} disabled={busy} aria-busy={busy} onClick={() => void resume()}>{busy ? "Resuming…" : "Resume research"}</button>
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
