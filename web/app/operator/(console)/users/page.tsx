"use client";
// Users (A4): every user across clients with their accounts (of the cap), questions in the last
// 30 days, last activity and access, with Terminate or Re-enable. Counts only, never question
// text. Ending access keeps the user's history and signs them out everywhere.
import { useState } from "react";
import { operatorRequest } from "@/lib/operator-api";
import type { ConsoleUser } from "@/lib/gen/api.gen";
import { useOperatorApi } from "../useOperatorApi";
import { ConsoleLoad } from "../ConsoleLoad";
import { Pill } from "@/ui/design";
import { expiresIn, lastActive, num } from "@/lib/console";

function access(u: ConsoleUser): { text: string; tone: "ok" | "warn" | "neutral" } {
  if (u.access !== "active") return { text: "Access ended", tone: "neutral" };
  if (u.expires_in_days !== null && u.expires_in_days <= 3) return { text: expiresIn(u.expires_in_days), tone: "warn" };
  return { text: "Active", tone: "ok" };
}

export default function Users() {
  const data = useOperatorApi("GET /operator/v1/console/users");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  if (!data.data) return <ConsoleLoad title="Users" state={data} />;
  const rows = data.data.users;

  const change = async (u: ConsoleUser) => {
    const end = u.access === "active";
    if (end && !window.confirm(`End access for ${u.email}? Their history is kept and they are signed out everywhere.`)) return;
    const key = `${u.tenant_id}/${u.user_id}`;
    setBusy(key);
    setMessage(null);
    try {
      await operatorRequest(end ? "POST /operator/v1/console/users/{tenant_id}/terminate" : "POST /operator/v1/console/users/{tenant_id}/re-enable", { params: { tenant_id: u.tenant_id }, body: { user_id: u.user_id } });
      setMessage(end ? `Access ended for ${u.email}.` : `Access restored for ${u.email}.`);
      data.reload();
    } catch {
      setMessage("The change was not saved. Try again.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="pg admin">
      <div className="head"><div className="ttl"><h1>Users</h1><div className="sub">Usage and access across all clients. Usage is counts only, never question text.</div></div></div>
      {message ? <p className="sm-text" role="status">{message}</p> : null}
      <div className="card tscroll">
        <div className="trow th cols-users"><span>User</span><span>Client</span><span>Accounts</span><span>Questions (30d)</span><span>Last active</span><span>Access</span></div>
        {rows.map((u) => {
          const a = access(u);
          const key = `${u.tenant_id}/${u.user_id}`;
          return (
            <div key={key} className="trow cols-users">
              <b>{u.email}</b>
              <span>{u.client}</span>
              <span>{num(u.accounts)} of {num(u.account_cap)}</span>
              <span>{num(u.questions)}</span>
              <span>{lastActive(u.last_active_at)}</span>
              <span className="stack-6">
                <Pill tone={a.tone} small>{a.text}</Pill>
                <button type="button" className={u.access === "active" ? "link-danger sm-text align-start" : "link-strong sm-text align-start"} disabled={busy === key} onClick={() => void change(u)}>
                  {u.access === "active" ? "Terminate" : "Re-enable"}
                </button>
              </span>
            </div>
          );
        })}
        {!rows.length ? <div className="state"><h2>No users yet</h2><p>Users appear here once a client invites them.</p></div> : null}
      </div>
      <p className="mut xs-text">Terminating or letting access expire keeps the user&apos;s history. Re-enable restores access in one click.</p>
    </div>
  );
}
