"use client";
// Support access (admins only): grant one named support engineer time-boxed, read-only access,
// revoke it, and see every support action.
import { useState } from "react";
import { LifeBuoy } from "lucide-react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { humanize } from "@/lib/ws-format";
import { Badge, Banner, PageHead, Section } from "@/ui/primitives";

interface Grant { id: string; grantee: string; scope: string; reason: string; granted_by: string; granted_at: string; expires_at: string; revoked_at: string | null }
interface Event { grant_id: string; operator: string; method: string; route: string; outcome: string; at: string }
const REASONS = ["customer_request", "incident_investigation", "onboarding_help", "data_question"];

function Support({ s }: { s: Session }) {
  const admin = s.role === "admin";
  const grants = useApi<{ grants: Grant[] }>(admin ? "/v1/support/grants" : null);
  const events = useApi<{ events: Event[] }>(admin ? "/v1/support/events" : null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [now] = useState(() => Date.now());
  const [grantee, setGrantee] = useState(""); const [reason, setReason] = useState(REASONS[0]); const [minutes, setMinutes] = useState(30);
  if (!admin) return <div className="content"><PageHead title="Support access" /><Banner kind="neutral">Only admins manage support access.</Banner></div>;
  const report = (err: unknown) => setMsg({ ok: false, text: err instanceof ApiError ? (err.type === "step_up_required" ? "Sign in again with multi-factor authentication to grant access." : `Refused (${err.type}).`) : "Refused." });
  const grant = async (e: React.FormEvent) => { e.preventDefault(); setMsg(null); try { await api("POST", "/v1/support/grants", { grantee: grantee.trim(), scope: "read", reason_code: reason, minutes }); setGrantee(""); setMsg({ ok: true, text: "Access granted." }); grants.reload(); } catch (err) { report(err); } };
  const revoke = async (g: Grant) => { setMsg(null); try { await api("POST", `/v1/support/grants/${encodeURIComponent(g.id)}/revoke`); grants.reload(); events.reload(); setMsg({ ok: true, text: "Access revoked." }); } catch (err) { report(err); } };
  const grantRows = grants.data?.grants ?? [];
  const eventRows = events.data?.events ?? [];
  return (
    <div className="content">
      <PageHead title="Support access" sub="Support sees your workspace only when you grant it, read-only and for a limited time. Every action they take is listed here." />
      {msg ? <p className={msg.ok ? "inline-status block-gap" : "inline-error block-gap"} role="status">{msg.text}</p> : null}
      <Section title="Grant access">
        <form className="card form-row" onSubmit={(e) => void grant(e)} aria-label="Grant support access">
          <div className="field"><label htmlFor="grantee">Support engineer</label><input id="grantee" className="input" value={grantee} onChange={(e) => setGrantee(e.target.value)} required placeholder="name@6th.example" /></div>
          <div className="field">
            <label htmlFor="support-reason">Reason</label>
            <select id="support-reason" aria-label="Reason" className="select" value={reason} onChange={(e) => setReason(e.target.value)}>{REASONS.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}</select>
          </div>
          <div className="field"><label htmlFor="minutes">Minutes</label><input id="minutes" className="input num" type="number" min={1} max={120} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} /></div>
          <button className="btn primary" type="submit"><LifeBuoy size={16} aria-hidden="true" />Grant read-only access</button>
        </form>
      </Section>
      <Section title="Grants">
        {grantRows.length === 0 ? <p className="ink-2">No grants yet.</p> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Engineer</th><th scope="col">Reason</th><th scope="col">Granted by</th><th scope="col">Expires</th><th scope="col">State</th></tr></thead>
              <tbody>{grantRows.map((g) => {
                const live = !g.revoked_at && Date.parse(g.expires_at) > now;
                return (
                  <tr key={g.id}><td>{cellText(g.grantee)}</td><td>{humanize(cellText(g.reason))}</td><td className="mono t-footnote">{cellText(g.granted_by)}</td><td>{new Date(g.expires_at).toLocaleString()}</td>
                    <td>{live ? <button type="button" className="btn sm danger" onClick={() => void revoke(g)}>Revoke</button> : <Badge tone="butter">{g.revoked_at ? "Revoked" : "Expired"}</Badge>}</td></tr>
                );
              })}</tbody>
            </table>
          </div>
        )}
      </Section>
      <Section title="Support actions">
        {eventRows.length === 0 ? <p className="ink-2">No support actions recorded.</p> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">When</th><th scope="col">Engineer</th><th scope="col">Action</th><th scope="col">Outcome</th></tr></thead>
              <tbody>{eventRows.map((e, i) => (
                <tr key={`${e.at}-${i}`}><td>{new Date(e.at).toLocaleString()}</td><td>{cellText(e.operator)}</td><td className="mono t-footnote">{cellText(`${e.method} ${e.route}`)}</td><td><Badge tone={e.outcome === "ok" ? "mint" : "rose"}>{e.outcome === "ok" ? "Allowed" : "Refused"}</Badge></td></tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}

export default function SupportPage() { return <SignedIn>{(s) => <Support s={s} />}</SignedIn>; }
