"use client";
// View as a client: with the reference a client's administrator gave (a support grant to this
// staff member), open that client's workspace read-only for support. A banner stays on screen;
// it shows accounts and counts only, never questions, answers or files; every view is recorded
// in the client's own audit log, which the client can see.
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { operatorRequest } from "@/lib/operator-api";
import type { ViewAsWorkspace } from "@/lib/gen/api.gen";
import { lastActive, num, started } from "@/lib/console";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export default function ViewAsClient() {
  const [grant, setGrant] = useState("");
  const [view, setView] = useState<ViewAsWorkspace | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!UUID.test(grant.trim())) { setError("Enter the reference the client's administrator sent you."); return; }
    setBusy(true);
    setError(null);
    try {
      setView(await operatorRequest("GET /operator/v1/view/workspace", { headers: { "X-Support-Grant": grant.trim() } }));
    } catch (cause) {
      setError(cause instanceof ApiError && cause.status === 403 ? "That reference is not an active grant to you. Ask the client's administrator for access." : "The workspace could not be opened. Try again.");
    } finally {
      setBusy(false);
    }
  };

  if (!view) {
    return (
      <div className="pg admin">
        <div className="head"><div className="ttl"><h1>View as a client</h1><div className="sub">Read-only, for support, and only with access the client&apos;s administrator gave you.</div></div></div>
        <form className="card pad stack-14" onSubmit={(e) => void open(e)}>
          <div className="field"><label htmlFor="grant">Reference from the client&apos;s administrator</label><input id="grant" className="input" value={grant} onChange={(e) => setGrant(e.target.value)} autoComplete="off" /></div>
          {error ? <p className="sm-text bad-text" role="alert">{error}</p> : null}
          <button type="submit" className="btn primary align-start" disabled={busy}>{busy ? "Opening" : "Open workspace"}</button>
        </form>
        <p className="mut xs-text">The client sees every view in its own audit log. Questions, answers and files are never shown here.</p>
      </div>
    );
  }
  return (
    <>
      <div className="viewas-banner" role="status">
        <span>Viewing {view.client} as support · read-only · access ends {started(view.expires_at).replace("Today ", "at ")}</span>
        <button type="button" className="btn sm" onClick={() => { setView(null); setGrant(""); }}>Leave</button>
      </div>
      <div className="pg admin">
        <div className="head"><div className="ttl"><h1>{view.client}</h1><div className="sub">{view.users} user{view.users === 1 ? "" : "s"} · {num(view.accounts.length)} accounts · {num(view.credits_used)} of {num(view.credit_pool)} credits this month</div></div></div>
        <div className="card tscroll">
          <div className="trow th cols-runs"><span>Account</span><span>Lanes</span><span>Schedule</span><span>People</span><span>Signals</span><span>Last researched</span></div>
          {view.accounts.map((a) => (
            <div key={a.domain} className="trow cols-runs">
              <b>{a.name}<span className="sub">{a.domain}</span></b>
              <span>{a.lanes.join(", ") || "None"}</span>
              <span>{a.schedule}</span>
              <span>{num(a.people)}</span>
              <span>{num(a.signals)}</span>
              <span>{a.last_researched_at ? lastActive(a.last_researched_at) : "Not yet"}</span>
            </div>
          ))}
          {!view.accounts.length ? <div className="state"><h2>No accounts yet</h2></div> : null}
        </div>
      </div>
    </>
  );
}
