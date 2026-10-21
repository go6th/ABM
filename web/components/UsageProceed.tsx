"use client";
// "This refresh will use about 2.5M tokens (about $11.00). Proceed?" with Proceed and Cancel
// (owner decision 2026-10-21, while the research caps are off): nothing starts until Proceed.
import type { UsageEstimate } from "@/lib/gen/api.gen";
import { dollars, tokenCount, usageBasis } from "@/lib/usage";

export function UsageProceed({ usage, what, busy, message, note, onProceed, onCancel }: {
  usage: UsageEstimate;
  /** "This refresh", "Researching Acme", ... */
  what: string;
  busy: boolean;
  message?: string | null;
  /** A line under the estimate, e.g. the researches left. */
  note?: React.ReactNode;
  onProceed: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="usage-ask" role="group" aria-label="Confirm the expected use">
      <p className="sm-text">
        {what} will use about <strong>{tokenCount(usage.tokens)} tokens</strong> (about <strong>{dollars(usage.cost_usd)}</strong>). Proceed?
      </p>
      <p className="mut xs-text">{usageBasis(usage)}</p>
      {note}
      {message ? <p className="sm-text" role="alert">{message}</p> : null}
      <div className="row-12">
        <button type="button" className="btn primary" disabled={busy} onClick={onProceed}>
          {busy ? "Starting…" : "Proceed"}
        </button>
        <button type="button" className="btn" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
