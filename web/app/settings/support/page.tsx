"use client";
// Support access (admins only): grant one named support engineer time-boxed, read-only access, revoke it, and see every support action.
import { useState } from "react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";

interface Grant { id: string; grantee: string; scope: string; reason: string; granted_by: string; granted_at: string; expires_at: string; revoked_at: string | null }
interface Event { grant_id: string; operator: string; method: string; route: string; outcome: string; at: string }
const REASONS = ["customer_request", "incident_investigation", "onboarding_help", "data_question"];

function Support({ s }: { s: Session }) {
  const admin = s.role === "admin";
  const grants = useApi<{ grants: Grant[] }>(admin ? "/v1/support/grants" : null);
  const events = useApi<{ events: Event[] }>(admin ? "/v1/support/events" : null);
  const [msg, setMsg] = useState<string | null>(null);
  const [now] = useState(() => Date.now());
  const [grantee, setGrantee] = useState(""); const [reason, setReason] = useState(REASONS[0]); const [minutes, setMinutes] = useState(30);
  if (!admin) return <p>Only admins manage support access.</p>;
  const report = (err: unknown) => setMsg(err instanceof ApiError ? (err.type === "step_up_required" ? "Sign in again with multi-factor authentication to grant access." : `Refused (${err.type}).`) : "Refused.");
  const grant = async (e: React.FormEvent) => { e.preventDefault(); try { await api("POST", "/v1/support/grants", { grantee: grantee.trim(), scope: "read", reason_code: reason, minutes }); setGrantee(""); setMsg("Access granted."); grants.reload(); } catch (err) { report(err); } };
  const revoke = async (g: Grant) => { try { await api("POST", `/v1/support/grants/${encodeURIComponent(g.id)}/revoke`); grants.reload(); events.reload(); } catch (err) { report(err); } };
  return (
    <section>
      <h1>Support access</h1>
      <p className="muted">Support can only see your workspace when you grant it, read-only, for a limited time. Every action they take is listed below.</p>
      {msg ? <p role="status">{msg}</p> : null}
      <form className="row" onSubmit={grant} aria-label="Grant support access">
        <label htmlFor="grantee">Support engineer</label><input id="grantee" value={grantee} onChange={(e) => setGrantee(e.target.value)} required />
        <select aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}>{REASONS.map((r) => <option key={r} value={r}>{r.replaceAll("_", " ")}</option>)}</select>
        <label htmlFor="minutes">Minutes</label><input id="minutes" type="number" min={1} max={120} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} />
        <button className="primary" type="submit">Grant read-only access</button>
      </form>
      <h2>Grants</h2>
      <table className="data"><thead><tr><th scope="col">Engineer</th><th scope="col">Reason</th><th scope="col">Granted by</th><th scope="col">Expires</th><th scope="col">State</th></tr></thead>
        <tbody>{(grants.data?.grants ?? []).map((g) => {
          const live = !g.revoked_at && Date.parse(g.expires_at) > now;
          return (<tr key={g.id}><td>{cellText(g.grantee)}</td><td>{cellText(g.reason)}</td><td>{cellText(g.granted_by)}</td><td>{new Date(g.expires_at).toLocaleString()}</td>
            <td>{live ? <button type="button" onClick={() => void revoke(g)}>Revoke</button> : <span className="muted">{g.revoked_at ? "Revoked" : "Expired"}</span>}</td></tr>);
        })}</tbody></table>
      <h2>Support actions</h2>
      <table className="data"><thead><tr><th scope="col">When</th><th scope="col">Engineer</th><th scope="col">Action</th><th scope="col">Outcome</th></tr></thead>
        <tbody>{(events.data?.events ?? []).map((e, i) => (
          <tr key={`${e.at}-${i}`}><td>{new Date(e.at).toLocaleString()}</td><td>{cellText(e.operator)}</td><td>{cellText(`${e.method} ${e.route}`)}</td><td>{e.outcome === "ok" ? "Allowed" : "Refused"}</td></tr>
        ))}</tbody></table>
    </section>
  );
}

export default function SupportPage() { return <SignedIn>{(s) => <Support s={s} />}</SignedIn>; }
