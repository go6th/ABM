"use client";
// Pilot feedback (PL1): a verdict and a reason from the fixed list (config/pilot.yaml), with an
// optional note stored as tenant data.
import { useId, useState } from "react";
import { api, ApiError } from "@/lib/api";

const REASONS: Record<"useful" | "not_useful", string[]> = {
  useful: ["accurate", "new_to_me", "used_on_call", "saved_time"],
  not_useful: ["wrong", "out_of_date", "already_known", "not_relevant", "missing_source"],
};

/** "new_to_me" reads "New to me". */
export function reasonLabel(code: string): string {
  const text = code.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export interface FeedbackTarget { domain: string; runId?: string; table?: string; rowId?: string }

export function FeedbackForm({ target, label }: { target: FeedbackTarget; label: string }) {
  const id = useId();
  const [verdict, setVerdict] = useState<"useful" | "not_useful">("useful");
  const [reason, setReason] = useState(REASONS.useful[0]);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api("POST", "/v1/feedback", { domain: target.domain, run_id: target.runId, table: target.table, row_id: target.rowId, verdict, reason, note: note.trim() || undefined });
      setNote("");
      setMsg({ ok: true, text: "Thanks, feedback saved." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? `Feedback refused (${err.type}).` : "Feedback refused." });
    }
  };
  return (
    <form className="form" onSubmit={(e) => void send(e)} aria-label={label}>
      <fieldset className="field">
        <legend className="strong">{label}</legend>
        <div className="seg block" role="group" aria-label="Verdict">
          {(["useful", "not_useful"] as const).map((v) => (
            <button key={v} type="button" aria-pressed={verdict === v} onClick={() => { setVerdict(v); setReason(REASONS[v][0]); }}>
              {v === "useful" ? "Useful" : "Not useful"}
            </button>
          ))}
        </div>
      </fieldset>
      <div className="field">
        <label htmlFor={`${id}-reason`}>Reason</label>
        <select id={`${id}-reason`} className="select" value={reason} onChange={(e) => setReason(e.target.value)}>
          {REASONS[verdict].map((r) => <option key={r} value={r}>{reasonLabel(r)}</option>)}
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${id}-note`}>Note (optional)</label>
        <textarea id={`${id}-note`} className="textarea" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className="row"><button className="btn primary" type="submit">Send feedback</button></div>
      {msg ? <p className={msg.ok ? "inline-status" : "inline-error"} role="status">{msg.text}</p> : null}
    </form>
  );
}

/** A usefulness event (D-68, identifiers only); a refusal (not a Confirmed fact) is ignored. */
export function markUseful(kind: "first_call_question_used" | "confirmed_fact_referenced", subjectId: string, via: "marked" | "copied" | "opened" | "noted"): void {
  void api("POST", "/v1/usefulness", { kind, subject_id: subjectId, via }).catch(() => undefined);
}
