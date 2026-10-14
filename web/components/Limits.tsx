"use client";
// Limits per user (owner decision 2026-10-14): a quiet line such as "1 of 2 researches left this
// month" where it matters, and, when a limit is used up, the refusal in plain words with
// "Request more from admin" (an in-app request; the user sees "Request sent").
import { useCallback, useEffect, useId, useState } from "react";
import { api } from "@/lib/api";
import { limitLine, type LimitKind } from "@/lib/limits";
import type { LimitRequestSent, MyLimits } from "@/lib/gen/api.gen";

export { limitLine, limitReached, type LimitKind } from "@/lib/limits";

/** The caller's limits; ``reload`` after an action that used one. */
export function useMyLimits(): { limits: MyLimits | null; reload: () => void } {
  const [limits, setLimits] = useState<MyLimits | null>(null);
  const reload = useCallback(() => {
    api<MyLimits>("GET", "/v1/me/limits").then(setLimits).catch(() => undefined);
  }, []);
  useEffect(() => { reload(); }, [reload]);
  return { limits, reload };
}

export function LimitNote({ limits, kind }: { limits: MyLimits | null; kind: LimitKind }) {
  if (!limits) return null;
  return <p className="mut xs-text limit-note">{limitLine(limits, kind)}</p>;
}

/** The used-up limit's message and "Request more from admin" with an optional note. */
export function LimitReached({ kind, message, pending = false }: { kind: LimitKind; message: string; pending?: boolean }) {
  const noteId = useId();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "failed">(pending ? "sent" : "idle");

  const send = async () => {
    setState("sending");
    try {
      await api<LimitRequestSent>("POST", "/v1/limits/requests", { kind, ...(note.trim() ? { note: note.trim() } : {}) });
      setState("sent");
    } catch {
      setState("failed");
    }
  };

  return (
    <div className="notice warn stack-10" role="status">
      <p>{message}</p>
      {state === "sent" ? (
        <p className="sm-text"><b>Request sent.</b> You will see the answer here when your admin replies.</p>
      ) : open ? (
        <div className="stack-10">
          <div className="field">
            <label htmlFor={noteId}>Note for your admin (optional)</label>
            <textarea id={noteId} className="textarea" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="row-12 wrap">
            <button type="button" className="btn primary sm" disabled={state === "sending"} onClick={() => void send()}>{state === "sending" ? "Sending" : "Send request"}</button>
            <button type="button" className="btn sm" onClick={() => setOpen(false)}>Cancel</button>
          </div>
          {state === "failed" ? <p className="sm-text bad-text" role="alert">The request was not sent. Try again.</p> : null}
        </div>
      ) : (
        <button type="button" className="btn sm align-start" onClick={() => setOpen(true)}>Request more from admin</button>
      )}
    </div>
  );
}
