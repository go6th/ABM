"use client";
// Support access: the grant form (who, why, how long), the grants with Revoke, and every support
// view. Giving access needs a recent multi-factor sign-in; the server says so when it is missing.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { GrantCreated, SupportEvent, SupportGrant } from "@/lib/gen/api.gen";
import { Pill } from "@/ui/design";
import { lastActive, started } from "@/lib/console";

const REASONS = [
  ["customer_request", "We asked for help"],
  ["onboarding_help", "Getting started"],
  ["data_question", "A question about our data"],
  ["incident_investigation", "Investigating a problem"],
] as const;
const DURATIONS = [[30, "30 minutes"], [60, "1 hour"], [120, "2 hours"]] as const;

export function SupportAccess({ grants, events }: { grants: SupportGrant[]; events: SupportEvent[] }) {
  const router = useRouter();
  const [grantee, setGrantee] = useState("");
  const [reason, setReason] = useState<string>(REASONS[0][0]);
  const [minutes, setMinutes] = useState<number>(60);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const now = new Date();
  const live = (g: SupportGrant) => !g.revoked_at && new Date(g.expires_at) > now;

  const give = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const made = await api<GrantCreated>("POST", "/v1/support/grants", { grantee: grantee.trim().toLowerCase(), scope: "read", reason_code: reason, minutes });
      setMessage({ ok: true, text: `Access given. Send this reference to 6th support: ${made.grant_id}` });
      setGrantee("");
      router.refresh();
    } catch (cause) {
      const text = cause instanceof ApiError && cause.status === 403
        ? "Sign in again with multi-factor authentication, then give access."
        : cause instanceof ApiError && cause.status === 422 ? "Enter the 6th person's email address." : "Access was not given. Try again.";
      setMessage({ ok: false, text });
    } finally {
      setBusy(false);
    }
  };
  const revoke = async (id: string) => {
    if (!window.confirm("Revoke this support access? Support staff lose access to your workspace straight away.")) return;
    setBusy(true);
    try {
      await api("POST", `/v1/support/grants/${id}/revoke`);
      router.refresh();
    } catch {
      setMessage({ ok: false, text: "It could not be revoked. Try again." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <form className="card pad stack-14" onSubmit={(e) => void give(e)} aria-labelledby="give-title">
        <h2 id="give-title" className="card-title">Give read-only access</h2>
        <div className="field"><label htmlFor="grantee">6th person&apos;s email</label><input id="grantee" className="input" type="email" required maxLength={120} value={grantee} onChange={(e) => setGrantee(e.target.value)} /></div>
        <div className="row-12 wrap align-top">
          <div className="field grow-220"><label htmlFor="reason">Why</label><select id="reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)}>{REASONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
          <div className="field grow-220"><label htmlFor="minutes">For how long</label><select id="minutes" className="input" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>{DURATIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
        </div>
        {message ? <p className={`sm-text ${message.ok ? "" : "bad-text"}`} role={message.ok ? "status" : "alert"}>{message.text}</p> : null}
        <button type="submit" className="btn primary align-start" disabled={busy}>Give access</button>
      </form>
      <section className="card list" aria-labelledby="grants-title">
        <h2 id="grants-title" className="card-title lrow">Access given</h2>
        {grants.length ? grants.map((g) => (
          <div key={g.id} className="lrow">
            <span className="grow">{g.grantee}<span className="sub mut xs-text">Reference {g.id}</span></span>
            {live(g) ? <Pill tone="ok" small>Until {started(g.expires_at).replace("Today ", "")}</Pill> : <Pill small>{g.revoked_at ? "Revoked" : "Ended"}</Pill>}
            {live(g) ? <button type="button" className="link-danger sm-text" disabled={busy} onClick={() => void revoke(g.id)}>Revoke</button> : null}
          </div>
        )) : <div className="lrow mut">No one has been given access.</div>}
      </section>
      <section className="card list" aria-labelledby="events-title">
        <h2 id="events-title" className="card-title lrow">What support viewed</h2>
        {events.length ? events.map((e, i) => (
          <div key={`${e.grant_id}-${i}`} className="lrow">
            <span className="grow">{e.operator} · {e.route.replace("/operator/v1/view/", "")}</span>
            <span className="when">{lastActive(e.at)}</span>
            <Pill tone={e.outcome === "ok" ? "neutral" : "warn"} small>{e.outcome === "ok" ? "Viewed" : "Refused"}</Pill>
          </div>
        )) : <div className="lrow mut">Nothing yet.</div>}
      </section>
    </>
  );
}
