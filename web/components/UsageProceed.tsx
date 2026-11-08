"use client";
// The confirm before anything that spends money (owner's rules 2026-10-21 and 2026-11-05: show
// the expected tokens and dollars, then ask "Proceed?" and "Are you sure?"; the server refuses a
// start without both confirmations, ``runs.start_guard``). Redesigned 2026-11-07 so the two
// questions no longer swap the screen: the cost line is always shown above one button. The first
// press (Proceed) arms it: it glows, says what a yes starts, and Cancel appears. The second press
// (Are you sure) starts it. Changing the form (the parent passes a new ``key``) or Cancel disarms.
// What the research cannot do yet (people consent missing) is listed above with where to fix it.
import Link from "next/link";
import { useRef, useState, type ReactNode } from "react";
import type { ResearchGap, UsageEstimate } from "@/lib/gen/api.gen";
import { dollars, tokenCount, usageBasis } from "@/lib/usage";

/** The two confirmations every research start sends; spread into the request body only from
 * the handler that runs after the second press. */
export const CONFIRMED_TWICE = { confirm: true, confirm_again: true } as const;

/** The second question on its own, for a start whose first question was asked elsewhere
 * (Resume asks "Resume?" on its own button first). */
export function SecondConfirm({ cost, doing = "starts research on the web", busy, message, yes = "Yes, start it", onYes, onNo }: {
  /** "about $11.00", or null when there is no estimate. */
  cost: string | null;
  /** What saying yes starts, after "This": "starts research on the web" by default. */
  doing?: string;
  busy: boolean;
  message?: string | null;
  yes?: string;
  onYes: () => void;
  onNo: () => void;
}) {
  return (
    <div className="spend armed" role="group" aria-label="Confirm again">
      <p className="spend-ask" role="status">
        <strong>Are you sure?</strong> This {doing} and spends real money{cost ? <> ({cost})</> : null}. It cannot be undone once it starts.
      </p>
      {message ? <p className="notice error" role="alert">{message}</p> : null}
      <div className="spend-actions">
        <button type="button" className="btn primary go" autoFocus /* the step replaced the button that had focus */ aria-busy={busy || undefined} onClick={() => { if (!busy) onYes(); }}>
          {busy ? "Starting…" : yes}
        </button>
        <button type="button" className="btn" disabled={busy} onClick={onNo}>Cancel</button>
      </div>
    </div>
  );
}

export function UsageProceed({ usage, what, doing = "starts research on the web", label, busy, disabled = false, reason, message, note, gaps, extra, onProceed, onCancel }: {
  /** The expected use; null when it could not be estimated (both questions are still asked). */
  usage: UsageEstimate | null;
  /** "This refresh", "Researching Acme", ... */
  what: string;
  /** What a yes starts, after "This": "starts research on the web" by default. */
  doing?: string;
  /** The button before it is armed: "Start research", "Refresh", "Analyze file". */
  label: string;
  busy: boolean;
  /** The form is not ready; ``reason`` says why, under the button. */
  disabled?: boolean;
  reason?: ReactNode;
  message?: string | null;
  /** A line under the estimate, e.g. the researches left. */
  note?: ReactNode;
  /** What this research cannot do yet, each with where to fix it. */
  gaps?: ResearchGap[];
  /** Beside the button while it is not armed, e.g. "Not now". */
  extra?: ReactNode;
  onProceed: () => void;
  onCancel?: () => void;
}) {
  const [armed, setArmed] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const cost = usage ? dollars(usage.cost_usd) : null;
  const disarm = () => { setArmed(false); onCancel?.(); button.current?.focus(); };
  const press = () => {
    if (busy || disabled) return;
    if (!armed) { setArmed(true); return; }
    onProceed();
  };
  return (
    <div className={armed ? "spend armed" : "spend"} role="group" aria-label="Confirm the expected use">
      <p className="spend-cost">
        {usage ? (
          <>{what} uses about <strong>{tokenCount(usage.tokens)} tokens</strong> (about <strong>{cost}</strong>).</>
        ) : (
          <>{what} spends real money. The cost could not be estimated.</>
        )}
        {usage ? <span className="mut xs-text"> {usageBasis(usage)}</span> : null}
      </p>
      {note}
      {gaps && gaps.length > 0 ? (
        <ul className="spend-gaps" aria-label="Before you start">
          {gaps.map((g) => (
            <li key={g.code} className="sm-text">
              {g.text}
              {g.fix_path ? <> <Link href={g.fix_path} target="_blank" rel="noopener">Fix it first</Link></> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {armed ? (
        <p className="spend-ask" role="status">
          <strong>Are you sure?</strong> This {doing} and spends real money{cost ? <> (about {cost})</> : null}. It cannot be undone once it starts.
        </p>
      ) : null}
      {message ? <p className="notice error" role="alert">{message}</p> : null}
      <div className="spend-actions">
        <button
          ref={button}
          type="button"
          className={armed ? "btn primary wide go" : "btn primary wide"}
          disabled={disabled && !busy}
          aria-busy={busy || undefined}
          aria-describedby={disabled && reason ? "spend-reason" : undefined}
          onClick={press}
        >
          {busy ? "Starting…" : armed ? `Yes, start${cost ? ` · about ${cost}` : ""}` : label}
        </button>
        {armed ? <button type="button" className="btn" disabled={busy} onClick={disarm}>Cancel</button> : extra}
      </div>
      {disabled && reason ? <p className="mut xs-text" id="spend-reason">{reason}</p> : null}
    </div>
  );
}
