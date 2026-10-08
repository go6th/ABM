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
  const toggle = (rule: string, on: boolean) => setSt((x) => ({ ...x, immediate_rules: on ? [...x.immediate_rules, rule] : x.immediate_rules.filter((r) => r !== rule) }));
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    try { setSt(await api<Settings>("PUT", "/v1/subscriptions", st)); setMsg("Saved."); }
    catch (err) { setMsg(err instanceof ApiError ? `Refused (${err.type}).` : "Refused."); }
  };
  const all = async () => { try { setSt(await api<Settings>("POST", "/v1/subscriptions/subscribe-all")); setMsg("Subscribed to every immediate alert."); } catch { setMsg("Refused."); } };
  return (
    <form className="card" onSubmit={save} aria-label="Alert settings">
      <label className="row"><input type="checkbox" checked={st.digest} onChange={(e) => setSt({ ...st, digest: e.target.checked })} /> Weekly digest</label>
      <fieldset><legend>Immediate alerts (email)</legend>
        {IMMEDIATE.map(([id, label]) => (
          <label key={id} className="row"><input type="checkbox" checked={st.immediate_rules.includes(id)} onChange={(e) => toggle(id, e.target.checked)} /> {label}</label>
        ))}
      </fieldset>
      <label className="row"><input type="checkbox" checked={st.slack} onChange={(e) => setSt({ ...st, slack: e.target.checked })} /> Also send immediate alerts to Slack</label>
      <label htmlFor="threshold">Minimum significance</label>
      <input id="threshold" type="number" min={0} max={1000} value={st.threshold} onChange={(e) => setSt({ ...st, threshold: Number(e.target.value) })} />
      <div className="row"><button className="primary" type="submit">Save</button><button type="button" onClick={() => void all()}>Subscribe to all immediate alerts</button></div>
      {msg ? <p role="status">{msg}</p> : null}
    </form>
  );
}

function Budget() {
  const b = useApi<{ period: string; percent: number }>("/v1/monitoring/budget");
  if (!b.data) return null;
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
    <section>
      <h1>Alert settings</h1>
      {cur.data ? <Form initial={cur.data} /> : <p className="muted">Loading</p>}
      {s.role === "admin" ? <Budget /> : null}
    </section>
  );
}

export default function AlertSettingsPage() { return <SignedIn>{(s) => <AlertSettings s={s} />}</SignedIn>; }
