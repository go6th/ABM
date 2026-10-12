"use client";
// Alert preferences (subscribe up, AL3): the digest is on by default; immediate rules, chat app
// delivery and a threshold are switched on here. Admins also see the monitoring allowance, in
// percent only.
import { useState } from "react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { ConfirmButton } from "@/ui/ConfirmButton";
import { LoadError, Meter, PageHead, PageSkeleton, Section } from "@/ui/primitives";

interface Settings { digest: boolean; immediate_rules: string[]; slack: boolean; threshold: number; muted_accounts: string[] }
const IMMEDIATE: [string, string][] = [["new_build_role", "New build or admin role in your lanes"], ["leadership_change", "Technology or commercial leadership change"], ["tier_one_news", "Verified tier-one news"], ["funding_ma", "Funding or acquisition"]];

function Form({ initial }: { initial: Settings }) {
  const [st, setSt] = useState<Settings>(initial);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const toggle = (rule: string, on: boolean) => setSt((x) => ({ ...x, immediate_rules: on ? [...x.immediate_rules, rule] : x.immediate_rules.filter((r) => r !== rule) }));
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try { setSt(await api<Settings>("PUT", "/v1/subscriptions", st)); setMsg({ ok: true, text: "Alert preferences saved." }); }
    catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? `Alert preferences were not saved (${err.type}).` : "Alert preferences were not saved." }); }
    finally { setBusy(false); }
  };
  const all = async () => {
    setBusy(true);
    setMsg(null);
    try {
      setSt(await api<Settings>("POST", "/v1/subscriptions/subscribe-all"));
      setMsg({ ok: true, text: "Subscribed to every immediate alert." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? `Subscription update was refused (${err.type}).` : "Subscription update was refused." });
    } finally { setBusy(false); }
  };
  return (
    <form onSubmit={(e) => void save(e)} aria-label="Alert settings">
      <fieldset className="stack" disabled={busy}>
        <div className="group">
          <label className="group-item">
            <span className="grow"><span className="title">Weekly digest</span><br /><span className="sub">One email a week with every change at your accounts.</span></span>
            <input className="switch" type="checkbox" checked={st.digest} onChange={(e) => setSt({ ...st, digest: e.target.checked })} />
          </label>
        </div>
        <fieldset>
          <legend className="group-head">Immediate alerts</legend>
          <div className="group">
            {IMMEDIATE.map(([id, label]) => (
              <label key={id} className="group-item">
                <span className="grow title">{label}</span>
                <input className="switch" type="checkbox" checked={st.immediate_rules.includes(id)} onChange={(e) => toggle(id, e.target.checked)} />
              </label>
            ))}
          </div>
        </fieldset>
        <div className="group">
          <label className="group-item">
            <span className="grow"><span className="title">Also send immediate alerts to Slack</span><br /><span className="sub">Only when your workspace has connected it.</span></span>
            <input className="switch" type="checkbox" checked={st.slack} onChange={(e) => setSt({ ...st, slack: e.target.checked })} />
          </label>
        </div>
        <div className="field">
          <label htmlFor="threshold">Minimum significance</label>
          <input id="threshold" className="input num" type="number" min={0} max={1000} value={st.threshold} onChange={(e) => setSt({ ...st, threshold: Number(e.target.value) })} aria-describedby="threshold-hint" />
          <p className="hint" id="threshold-hint">0 sends every alert. Higher numbers send fewer.</p>
        </div>
        <div className="row">
          <button className="btn primary" type="submit" disabled={busy}>{busy ? "Saving" : "Save preferences"}</button>
          <ConfirmButton label="Subscribe to all immediate alerts" tone="plain" disabled={busy} title="Subscribe to every immediate alert?" body="Every available immediate email alert is switched on for you." action="Subscribe" onConfirm={() => void all()} />
        </div>
      </fieldset>
      {msg ? <p className={msg.ok ? "inline-status block-gap" : "inline-error block-gap"} role="status">{msg.text}</p> : null}
    </form>
  );
}

function Budget() {
  const b = useApi<{ period: string; percent: number }>("/v1/monitoring/budget");
  if (b.error) return <LoadError reference={null} retryHref="/settings/alerts" />;
  if (!b.data) return <PageSkeleton />;
  const tone = b.data.percent >= 95 ? "full" : b.data.percent >= 80 ? "warn" : undefined;
  return (
    <div className="card stack">
      <div className="row between"><span className="strong">{b.data.period}</span><span className="num">{b.data.percent}% used</span></div>
      <Meter value={b.data.percent} max={100} tone={tone} label={`${b.data.percent}% of the monitoring allowance used`} />
      {b.data.percent >= 80 ? <p className="t-footnote ink-2">Monitoring of lower priority accounts is reduced first.</p> : null}
    </div>
  );
}

function AlertSettings({ s }: { s: Session }) {
  const cur = useApi<Settings>("/v1/subscriptions");
  return (
    <div className="reading">
      <PageHead title="Alert preferences" sub="Choose what reaches you and how. The digest is on unless you turn it off." />
      {cur.error ? <LoadError reference={null} retryHref="/settings/alerts" />
        : cur.data ? <Form key={JSON.stringify(cur.data)} initial={cur.data} />
          : <PageSkeleton width="reading" />}
      {s.role === "admin" ? <Section title="Monitoring allowance" sub="How much of this period's monitoring the workspace has used."><Budget /></Section> : null}
    </div>
  );
}

export default function AlertSettingsPage() { return <SignedIn>{(s) => <AlertSettings s={s} />}</SignedIn>; }
