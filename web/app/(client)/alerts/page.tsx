"use client";
// Alert inbox: the caller's alerts, Leads labelled, with a usefulness rating from a fixed list (AL8).
import { useState } from "react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";

interface Alert { id: string; rule: string; tier: string; lead: boolean; subject: string; body: string; created_at: string }
const REASONS: Record<"useful" | "not_useful", string[]> = {
  useful: ["acted_on", "new_to_me", "good_timing"],
  not_useful: ["already_known", "wrong", "not_relevant", "too_late", "too_many"],
};

function Rate({ alert }: { alert: Alert }) {
  const [verdict, setVerdict] = useState<"useful" | "not_useful">("useful");
  const [reason, setReason] = useState(REASONS.useful[0]);
  const [msg, setMsg] = useState<string | null>(null);
  const send = async () => {
    try { await api("POST", `/v1/alerts/${encodeURIComponent(alert.id)}/feedback`, { verdict, reason }); setMsg("Thanks."); }
    catch (err) { setMsg(err instanceof ApiError ? `Refused (${err.type}).` : "Refused."); }
  };
  return (
    <div className="row" role="group" aria-label={`Rate alert ${alert.subject}`}>
      <select aria-label="Verdict" value={verdict} onChange={(e) => { const v = e.target.value === "not_useful" ? "not_useful" : "useful"; setVerdict(v); setReason(REASONS[v][0]); }}>
        <option value="useful">Useful</option><option value="not_useful">Not useful</option>
      </select>
      <select aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}>{REASONS[verdict].map((r) => <option key={r} value={r}>{r.replaceAll("_", " ")}</option>)}</select>
      <button type="button" onClick={() => void send()}>Rate</button>
      {msg ? <span role="status">{msg}</span> : null}
    </div>
  );
}

function Inbox({ s }: { s: Session }) {
  const list = useApi<{ alerts: Alert[] }>("/v1/alerts");
  const alerts = list.data?.alerts ?? [];
  return (
    <section>
      <h1>Alerts</h1>
      <p className="muted">Signed in as {cellText(s.user_id)}. New users receive the weekly digest only; switch on immediate alerts under Alert settings.</p>
      {alerts.length === 0 ? <p className="muted">No alerts yet.</p> : null}
      <ul className="cards">{alerts.map((a) => (
        <li key={a.id} className="card">
          <h2>{cellText(a.subject)} {a.lead ? <span className="badge">Lead, not yet verified</span> : null} <span className="muted">{a.tier}</span></h2>
          <p>{cellText(a.body)}</p>
          <Rate alert={a} />
        </li>
      ))}</ul>
    </section>
  );
}

export default function AlertsPage() { return <SignedIn>{(s) => <Inbox s={s} />}</SignedIn>; }
