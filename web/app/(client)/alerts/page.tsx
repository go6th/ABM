"use client";
// Alert inbox: the caller's alerts, Leads labelled, with a usefulness rating from a fixed list (AL8).
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
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
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    setMsg(null);
    try { await api("POST", `/v1/alerts/${encodeURIComponent(alert.id)}/feedback`, { verdict, reason }); setMsg("Feedback saved."); }
    catch (err) { setMsg(err instanceof ApiError ? `Feedback was refused (${err.type}).` : "Feedback was refused."); }
    finally { setBusy(false); }
  };
  return (
    <div className="row" role="group" aria-label={`Rate alert ${alert.subject}`}>
      <select aria-label="Verdict" disabled={busy} value={verdict} onChange={(e) => { const v = e.target.value === "not_useful" ? "not_useful" : "useful"; setVerdict(v); setReason(REASONS[v][0]); }}>
        <option value="useful">Useful</option><option value="not_useful">Not useful</option>
      </select>
      <select aria-label="Reason" disabled={busy} value={reason} onChange={(e) => setReason(e.target.value)}>{REASONS[verdict].map((r) => <option key={r} value={r}>{r.replaceAll("_", " ")}</option>)}</select>
      <button className="btn sm" type="button" disabled={busy} onClick={() => void send()}>{busy ? "Saving…" : "Rate"}</button>
      {msg ? <span role="status">{msg}</span> : null}
    </div>
  );
}

function Inbox() {
  const list = useApi<{ alerts: Alert[] }>("/v1/alerts");
  const alerts = list.data?.alerts ?? [];
  return (
    <section className="client-dashboard">
      <header className="client-dashboard-heading"><div><p className="eyebrow">WORKSPACE SIGNALS</p><h1>Alerts</h1>
        <p className="secondary">Alerts are tenant-scoped. Configure digest and immediate delivery in alert preferences.</p></div></header>
      {list.error ? <div className="client-data-state error-box" role="alert"><strong>Alerts could not be loaded</strong><p>{list.error.detail}</p><button className="btn" type="button" onClick={list.reload}>Try again</button></div>
        : !list.data ? <p className="client-loading" role="status">Loading alerts…</p>
          : alerts.length === 0 ? <div className="client-empty-state"><h2>No alerts yet</h2><p>When a configured alert is delivered, it will appear here.</p><a className="btn" href="/settings/alerts">Review alert preferences</a></div>
            : <ul className="cards">{alerts.map((a) => (
        <li key={a.id} className="card">
          <h2>{cellText(a.subject)} {a.lead ? <span className="badge">Lead, not yet verified</span> : null} <span className="muted">{a.tier}</span></h2>
          {a.lead ? <p className="banner tone-butter">This is an unverified lead. Review supporting research before treating it as established.</p> : null}
          <p>{cellText(a.body)}</p>
          <p className="muted">{cellText(a.rule.replaceAll("_", " "))} · {new Date(a.created_at).toLocaleString()}</p>
          <Rate alert={a} />
        </li>
      ))}</ul>}
    </section>
  );
}

export default function AlertsPage() { return <SignedIn>{() => <Inbox />}</SignedIn>; }
