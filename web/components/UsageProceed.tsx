"use client";
// "This refresh will use about 2.5M tokens (about $11.00). Proceed?" with Proceed and Cancel
// (owner decision 2026-10-21, while the research caps are off): nothing starts until Proceed.
// Proceed then asks a second time, "Are you sure?" (owner's rule 2026-11-05, after an upload
// started a paid run unasked): onProceed runs only after both, and the server refuses a start
// that does not carry both confirmations (``runs.start_guard``).
import { useState } from "react";
import type { UsageEstimate } from "@/lib/gen/api.gen";
import { dollars, tokenCount, usageBasis } from "@/lib/usage";

/** The two confirmations every research start sends; spread into the request body only from
 * the handler that runs after the second "Yes". */
export const CONFIRMED_TWICE = { confirm: true, confirm_again: true } as const;

/** The second question, shared by every start (research, refresh, resume, file analysis). */
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
    <div className="usage-ask" role="group" aria-label="Confirm again">
      <p className="sm-text">
        <strong>Are you sure?</strong> This {doing} and spends real money{cost ? <> (<strong>{cost}</strong>)</> : null}. It cannot be undone once it starts.
      </p>
      {message ? <p className="sm-text" role="alert">{message}</p> : null}
      <div className="row-12">
        <button type="button" className="btn primary" disabled={busy} onClick={onYes}>
          {busy ? "Starting…" : yes}
        </button>
        <button type="button" className="btn" disabled={busy} onClick={onNo}>No, cancel</button>
      </div>
    </div>
  );
}

export function UsageProceed({ usage, what, doing, busy, message, note, onProceed, onCancel }: {
  /** The expected use; null when it could not be estimated (both questions are still asked). */
  usage: UsageEstimate | null;
  /** "This refresh", "Researching Acme", ... */
  what: string;
  /** What the second question says a yes starts (``SecondConfirm``). */
  doing?: string;
  busy: boolean;
  message?: string | null;
  /** A line under the estimate, e.g. the researches left. */
  note?: React.ReactNode;
  onProceed: () => void;
  onCancel: () => void;
}) {
  const [sure, setSure] = useState(false);
  if (sure) {
    return (
      <SecondConfirm
        cost={usage ? `about ${dollars(usage.cost_usd)}` : null}
        doing={doing}
        busy={busy}
        message={message}
        onYes={onProceed}
        onNo={() => { setSure(false); onCancel(); }}
      />
    );
  }
  return (
    <div className="usage-ask" role="group" aria-label="Confirm the expected use">
      {usage ? (
        <>
          <p className="sm-text">
            {what} will use about <strong>{tokenCount(usage.tokens)} tokens</strong> (about <strong>{dollars(usage.cost_usd)}</strong>). Proceed?
          </p>
          <p className="mut xs-text">{usageBasis(usage)}</p>
        </>
      ) : (
        <p className="sm-text">{what} spends real money. The cost could not be estimated. Proceed?</p>
      )}
      {note}
      {message ? <p className="sm-text" role="alert">{message}</p> : null}
      <div className="row-12">
        <button type="button" className="btn primary" disabled={busy} onClick={() => setSure(true)}>Proceed</button>
        <button type="button" className="btn" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
