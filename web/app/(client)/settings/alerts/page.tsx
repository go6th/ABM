"use client";
// Alert settings (subscribe up, AL3): digest on by default; immediate rules, Slack and a threshold are switched on here. Admins also see the monitoring allowance in percent only.
import { useState } from "react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";

interface Settings { digest: boolean; immediate_rules: string[]; slack: boolean; threshold: number; muted_accounts: string[] }
const IMMEDIATE: [string, string][] = [["new_build_role", "New build or admin role in your lanes"], ["leadership_change", "Technology or commercial leadership change"], ["tier_one_news", "Verified tier-one news"], ["funding_ma", "Funding or acquisition"]];

function Form({ initial }: { initial: Settings }) {
  const [st, setSt] = useState<Settings>(initial);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const toggle = (rule: string, on: boolean) => setSt((x) => ({ ...x, immediate_rules: on ? [...x.immediate_rules, rule] : x.immediate_rules.filter((r) => r !== rule) }));
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try { setSt(await api<Settings>("PUT", "/v1/subscriptions", st)); setMsg("Alert preferences saved."); }
    catch (err) { setMsg(err instanceof ApiError ? `Alert preferences were not saved (${err.type}).` : "Alert preferences were not saved."); }
    finally { setBusy(false); }
  };
  const all = async () => {
    if (!window.confirm("Enable every available immediate email alert for this workspace?")) return;
    setBusy(true);
    setMsg(null);
    try {
      setSt(await api<Settings>("POST", "/v1/subscriptions/subscribe-all"));
      setMsg("Subscribed to every immediate alert returned by the service.");
    } catch (err) {
      setMsg(err instanceof ApiError ? `Subscription update was refused (${err.type}).` : "Subscription update was refused.");
    } finally { setBusy(false); }
  };
  return (
    <form className="card" onSubmit={save} aria-label="Alert settings">
      <fieldset disabled={busy}>
      <label className="row"><input type="checkbox" checked={st.digest} onChange={(e) => setSt({ ...st, digest: e.target.checked })} /> Weekly digest</label>
      <fieldset><legend>Immediate alerts</legend>
        {IMMEDIATE.map(([id, label]) => (
          <label key={id} className="row"><input type="checkbox" checked={st.immediate_rules.includes(id)} onChange={(e) => toggle(id, e.target.checked)} /> {label}</label>
        ))}
      </fieldset>
      <label className="row"><input type="checkbox" checked={st.slack} onChange={(e) => setSt({ ...st, slack: e.target.checked })} /> Also send immediate alerts to Slack, if connected</label>
      <label htmlFor="threshold">Minimum significance</label>
      <input id="threshold" type="number" min={0} max={1000} value={st.threshold} onChange={(e) => setSt({ ...st, threshold: Number(e.target.value) })} />
      <div className="row"><button className="btn primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Save preferences"}</button><button className="btn" type="button" disabled={busy} onClick={() => void all()}>Subscribe to all immediate alerts</button></div>
      </fieldset>
      {msg ? <p role="status">{msg}</p> : null}
    </form>
  );
}

function Budget() {
  const b = useApi<{ period: string; percent: number }>("/v1/monitoring/budget");
  if (b.error) return <div className="client-data-state error-box" role="alert"><strong>Monitoring allowance could not be loaded</strong><p>{b.error.detail}</p><button className="btn" type="button" onClick={b.reload}>Try again</button></div>;
  if (!b.data) return <p className="client-loading" role="status">Loading monitoring allowance…</p>;
  return (
    <div className="card">
      <h2>Monitoring allowance</h2>
      <label htmlFor="meter">{b.data.period}: {b.data.percent}% used</label>
      <meter id="meter" min={0} max={100} low={80} high={95} value={b.data.percent}>{b.data.percent}%</meter>
      {b.data.percent >= 80 ? <p className="muted">Monitoring of lower-priority accounts is reduced first.</p> : null}
    </div>
  );
}

function AlertSettings({ s }: { s: Session }) {
  const cur = useApi<Settings>("/v1/subscriptions");
  return (
    <section className="client-dashboard">
      <header className="client-dashboard-heading"><div><p className="eyebrow">WORKSPACE PREFERENCES</p><h1>Alert settings</h1>
        <p className="secondary">Control digest and immediate signal delivery for this workspace.</p></div></header>
      {cur.error ? <div className="client-data-state error-box" role="alert"><strong>Alert settings could not be loaded</strong><p>{cur.error.detail}</p><button className="btn" type="button" onClick={cur.reload}>Try again</button></div>
        : cur.data ? <Form key={JSON.stringify(cur.data)} initial={cur.data} />
          : <p className="client-loading" role="status">Loading alert settings…</p>}
      {s.role === "admin" ? <Budget /> : null}
    </section>
  );
}

export default function AlertSettingsPage() { return <SignedIn>{(s) => <AlertSettings s={s} />}</SignedIn>; }
