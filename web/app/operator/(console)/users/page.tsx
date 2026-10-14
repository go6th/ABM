"use client";
// Users (A4): every user across clients with their researches and chats this period (of their
// limits, with Change limits), last activity and access, with Terminate or Re-enable; and the
// Requests for more. Counts only, never question text. Ending access keeps the user's history
// and signs them out everywhere.
import { useState } from "react";
import { operatorRequest } from "@/lib/operator-api";
import type { ConsoleUser } from "@/lib/gen/api.gen";
import { useOperatorApi } from "../useOperatorApi";
import { RequestsList } from "../RequestsList";
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
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<{ researches: string; chats: string }>({ researches: "", chats: "" });
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

  const saveLimits = async (u: ConsoleUser) => {
    const key = `${u.tenant_id}/${u.user_id}`;
    setBusy(key);
    setMessage(null);
    try {
      await operatorRequest("PUT /operator/v1/console/users/{tenant_id}/limits", {
        params: { tenant_id: u.tenant_id },
        body: { user_id: u.user_id, research_limit: Math.max(0, Math.floor(Number(form.researches) || 0)), chat_limit: Math.max(0, Math.floor(Number(form.chats) || 0)) },
      });
      setMessage(`Limits changed for ${u.email}.`);
      setEditing(null);
      data.reload();
    } catch {
      setMessage("The limits were not saved. Try again.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="pg admin">
      <div className="head"><div className="ttl"><h1>Users</h1><div className="sub">Usage and access across all clients. Usage is counts only, never question text.</div></div></div>
      {message ? <p className="sm-text" role="status">{message}</p> : null}
      <div className="card tscroll">
        <div className="trow th cols-users-limits"><span>User</span><span>Client</span><span>Researches</span><span>Chats</span><span>Last active</span><span>Access</span></div>
        {rows.map((u) => {
          const a = access(u);
          const key = `${u.tenant_id}/${u.user_id}`;
          return (
            <div key={key}>
            <div className="trow cols-users-limits">
              <span className="stack-4">
                <b>{u.email}</b>
                <button type="button" className="link-strong sm-text align-start" aria-expanded={editing === key} onClick={() => { setEditing(editing === key ? null : key); setForm({ researches: String(u.research_limit), chats: String(u.chat_limit) }); }}>Change limits</button>
              </span>
              <span>{u.client}</span>
              <span>{num(u.researches_used)} of {num(u.research_limit)}</span>
              <span>{num(u.chats_used)} of {num(u.chat_limit)}</span>
              <span>{lastActive(u.last_active_at)}</span>
              <span className="stack-6">
                <Pill tone={a.tone} small>{a.text}</Pill>
                <button type="button" className={u.access === "active" ? "link-danger sm-text align-start" : "link-strong sm-text align-start"} disabled={busy === key} onClick={() => void change(u)}>
                  {u.access === "active" ? "Terminate" : "Re-enable"}
                </button>
              </span>
            </div>
            {editing === key ? (
              <div className="limits-edit" role="group" aria-label={`Limits for ${u.email}`}>
                <div className="field">
                  <label htmlFor={`${key}-r`}>Researches</label>
                  <input id={`${key}-r`} className="input sm num-input" type="number" min={0} value={form.researches} onChange={(e) => setForm((f) => ({ ...f, researches: e.target.value }))} />
                </div>
                <div className="field">
                  <label htmlFor={`${key}-c`}>Chats</label>
                  <input id={`${key}-c`} className="input sm num-input" type="number" min={0} value={form.chats} onChange={(e) => setForm((f) => ({ ...f, chats: e.target.value }))} />
                </div>
                <button type="button" className="btn primary sm" disabled={busy === key} onClick={() => void saveLimits(u)}>Save limits</button>
                <button type="button" className="btn sm" onClick={() => setEditing(null)}>Cancel</button>
              </div>
            ) : null}
            </div>
          );
        })}
        {!rows.length ? <div className="state"><h2>No users yet</h2><p>Users appear here once a client invites them.</p></div> : null}
      </div>
      <p className="mut xs-text">Terminating or letting access expire keeps the user&apos;s history. Re-enable restores access in one click.</p>
      <RequestsList />
    </div>
  );
}
