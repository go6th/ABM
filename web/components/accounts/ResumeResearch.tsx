"use client";
// A research that stopped before finishing, or stalled (owner decision 2026-10-14): what it
// holds is kept; "Resume research" continues it and reuses everything already found at no cost,
// so only the missing steps are paid for. It uses none of the user's researches. The button asks
// twice before anything starts (owner's rule 2026-11-05): "Resume this research?", then "Are you
// sure?".
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { CONFIRMED_TWICE, SecondConfirm } from "@/components/UsageProceed";
import { api, ApiError, errorText } from "@/lib/api";
import { announce } from "@/ui/Toaster";
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

/** The server's reason when it gave one in words, else the fallback: a bare status line such as
 * "Internal Server Error" is never shown to a person. */
export function reasonOr(cause: unknown, fallback: string): string {
  return errorText(cause, fallback);
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
  // 0: the button; 1: "Resume this research?"; 2: "Are you sure?" (only its Yes posts)
  const [step, setStep] = useState<0 | 1 | 2>(0);

  const resume = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setMessage(null);
    try {
      const resumed = await api<ResearchResumed>("POST", "/v1/research/resume", { run_id: runId, request_id: request.key({ run_id: runId }), ...CONFIRMED_TWICE });
      request.done();
      setDone(resumed);
      announce("Research resumed");
      onResumed?.(resumed);
      router.refresh();
    } catch (cause) {
      request.failed(cause);
      setMessage(reasonOr(cause, "The research could not be resumed. Try again in a moment."));
      setBusy(false);
      setStep(0);
    } finally {
      inFlight.current = false;
    }
  };

  if (done) return <ResumingNote resumed={done} compact={compact} />;
  if (step === 1) {
    return (
      <div className="usage-ask" role="group" aria-label="Confirm the resume">
        <p className="sm-text">Resume this research? It continues on the web and pays for the steps still missing.</p>
        <div className="row-12">
          <button type="button" className={`btn primary${compact ? " sm" : ""}`} onClick={() => setStep(2)}>Yes, resume</button>
          <button type="button" className={`btn${compact ? " sm" : ""}`} onClick={() => setStep(0)}>Cancel</button>
        </div>
      </div>
    );
  }
  if (step === 2) {
    return <SecondConfirm cost={null} busy={busy} message={message} yes="Yes, resume it" onYes={() => void resume()} onNo={() => setStep(0)} />;
  }
  return (
    <>
      {message ? <span className="sm-text bad-text" role="alert">{message}</span> : null}
      <button type="button" className={`btn primary${compact ? " sm" : " align-start"}`} disabled={busy} aria-busy={busy || undefined} onClick={() => { setMessage(null); setStep(1); }}>{busy ? "Resuming…" : "Resume research"}</button>
    </>
  );
}

/** The warning shown before a resume of a run whose saved answers were keyed by an older
 * version (ADR-0129): ``replay_extra_usd`` is the server's upper bound, None when nothing changed. */
export function replayWarning(extraUsd: number | null | undefined): string | null {
  if (extraUsd == null || extraUsd <= 0) return null;
  const dollars = `$${extraUsd.toFixed(2)}`;
  return `This resume may cost up to ${dollars} more: the platform changed how it recognises work already paid for, so some of it may be paid for again.`;
}

export function ResumeResearch({ stopped }: { stopped: StoppedRun }) {
  const [done, setDone] = useState<ResearchResumed | null>(null);
  if (done) return <ResumingNote resumed={done} />;
  const warning = replayWarning(stopped.replay_extra_usd);
  return (
    <section className="card pad stack-10" aria-labelledby="stopped-title">
      <div className="strong" id="stopped-title">{stopped.stalled ? "This research stalled" : "This research stopped before finishing"}</div>
      <p className="mut sm-text">
        {stopped.reason} Everything found so far is kept{stopped.steps_total ? ` (${stopped.steps_done} of ${stopped.steps_total} steps done)` : ""}. {warning ? "Resuming reuses what it can." : "Resuming reuses it at no cost."}
      </p>
      {warning ? <p className="sm-text bad-text" role="note">{warning}</p> : null}
      <ResumeButton runId={stopped.run_id} onResumed={setDone} />
    </section>
  );
}
