"use client";
// Pilot feedback (PL1): a verdict and a reason from the fixed list (config/pilot.yaml), with an optional note stored as tenant data.
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

const REASONS: Record<"useful" | "not_useful", string[]> = {
  useful: ["accurate", "new_to_me", "used_on_call", "saved_time"],
  not_useful: ["wrong", "out_of_date", "already_known", "not_relevant", "missing_source"],
};

export interface FeedbackTarget { domain: string; runId?: string; table?: string; rowId?: string }

export function FeedbackForm({ target, label }: { target: FeedbackTarget; label: string }) {
  const [verdict, setVerdict] = useState<"useful" | "not_useful">("useful");
  const [reason, setReason] = useState(REASONS.useful[0]);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api("POST", "/v1/feedback", { domain: target.domain, run_id: target.runId, table: target.table, row_id: target.rowId, verdict, reason, note: note.trim() || undefined });
      setNote(""); setMsg("Thanks, feedback saved.");
    } catch (err) { setMsg(err instanceof ApiError ? `Feedback refused (${err.type}).` : "Feedback refused."); }
  };
  return (
    <form className="card" onSubmit={send} aria-label={label}>
      <fieldset className="row"><legend>{label}</legend>
        {(["useful", "not_useful"] as const).map((v) => (
          <label key={v} className="row"><input type="radio" name={`verdict-${label}`} checked={verdict === v} onChange={() => { setVerdict(v); setReason(REASONS[v][0]); }} />{v === "useful" ? "Useful" : "Not useful"}</label>
        ))}
      </fieldset>
      <select aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}>{REASONS[verdict].map((r) => <option key={r} value={r}>{r.replaceAll("_", " ")}</option>)}</select>
      <label htmlFor={`note-${label}`}>Note (optional)</label>
      <textarea id={`note-${label}`} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
      <button type="submit">Send feedback</button>
      {msg ? <p role="status">{msg}</p> : null}
    </form>
  );
}

/** A usefulness event (D-68, identifiers only); a refusal (not a Confirmed fact) is ignored. */
export function markUseful(kind: "first_call_question_used" | "confirmed_fact_referenced", subjectId: string, via: "marked" | "copied" | "opened" | "noted"): void {
  void api("POST", "/v1/usefulness", { kind, subject_id: subjectId, via }).catch(() => undefined);
}
