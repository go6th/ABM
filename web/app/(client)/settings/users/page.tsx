"use client";
// Members and roles (admins only; changes need a fresh multi-factor sign-in).
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { DataTable, EmptyState, type DataColumn } from "@/design/components";

interface Member { user_id: string; role: string; status: string; version: number }
const ROLES = ["admin", "analyst", "seller", "viewer"];

function Users({ s }: { s: Session }) {
  const router = useRouter();
  const list = useApi<{ members: Member[] }>(s.role === "admin" ? "/v1/users" : null);
  const members = list.data?.members ?? [];
  const [msg, setMsg] = useState<string | null>(null);
  const [uid, setUid] = useState(""); const [role, setRole] = useState("viewer");
  const load = list.reload;
  if (s.role !== "admin") return <p>Only admins manage users.</p>;
  const report = (err: unknown) => setMsg(err instanceof ApiError ? (err.type === "step_up_required" ? "Sign in again with multi-factor authentication to change users." : `Refused (${err.type}).`) : "Refused.");
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    try { await api("POST", "/v1/users", { user_id: uid.trim(), role }); setUid(""); setMsg("Member added."); load(); }
    catch (err) { report(err); }
  };
  const change = async (m: Member, patch: { role?: string; status?: string }) => {
    if (patch.status === "removed" && !window.confirm(`Remove ${m.user_id} from this workspace?`)) return;
    setMsg(null);
    try { await api("PATCH", "/v1/users", { user_id: m.user_id, expected_version: m.version, ...patch }); setMsg("Member updated."); load(); }
    catch (err) { report(err); }
  };
  const columns: DataColumn<Member>[] = [
    { key: "identity", header: "Member", render: (member) => cellText(member.user_id) },
    {
      key: "role",
      header: "Role",
      render: (member) => <select aria-label={`Role of ${member.user_id}`} value={member.role} disabled={member.user_id === s.user_id}
        onChange={(event) => void change(member, { role: event.target.value })}>
        {ROLES.map((entry) => <option key={entry} value={entry}>{entry}</option>)}
      </select>,
    },
    { key: "status", header: "Status", render: (member) => cellText(member.status) },
    {
      key: "actions",
      header: "Actions",
      render: (member) => member.user_id === s.user_id ? <span className="muted">You</span> : (
        <div className="design-swatch-row">
          {member.status === "active"
            ? <button className="btn sm" type="button" onClick={() => void change(member, { status: "suspended" })}>Suspend</button>
            : <button className="btn sm" type="button" onClick={() => void change(member, { status: "active" })}>Reactivate</button>}
          <button className="btn sm danger" type="button" onClick={() => void change(member, { status: "removed" })}>Remove</button>
        </div>
      ),
    },
  ];
  return (
    <section className="client-dashboard">
      <header className="client-dashboard-heading"><div><p className="eyebrow">WORKSPACE ADMINISTRATION</p><h1>Team &amp; access</h1>
        <p className="secondary">Manage tenant membership and the backend-defined role assignments.</p></div></header>
      {msg ? <p role="alert">{msg}</p> : null}
      <form className="card" onSubmit={add} aria-label="Add a member">
        <h2>Add a member</h2>
        <div className="runs-filters">
          <label className="field" htmlFor="uid"><span className="label">Sign-in identity</span>
            <input className="input" id="uid" value={uid} onChange={(e) => setUid(e.target.value)} placeholder="connection:subject" required maxLength={200} />
          </label>
          <label className="field" htmlFor="role"><span className="label">Workspace role</span>
            <select id="role" className="select" value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select>
          </label>
        </div>
        <button className="btn primary" type="submit">Add member</button>
      </form>
      {list.error ? <div className="client-data-state error-box" role="alert"><strong>Team members could not be loaded</strong><p>{list.error.detail}</p><button className="btn" type="button" onClick={load}>Try again</button></div>
        : !list.data ? <p className="client-loading" role="status">Loading team members…</p>
          : members.length === 0 ? <EmptyState title="No members returned" description="The workspace API returned no member records." />
            : <DataTable caption="Workspace team members" columns={columns} rows={members} rowKey={(member) => member.user_id} />}
      <section className="card" aria-labelledby="sign-out-all">
        <h2 id="sign-out-all">Revoke workspace sessions</h2>
        <p className="secondary">Sign out all active sessions for this workspace account.</p>
        <button className="btn danger" type="button" onClick={() => {
          if (!window.confirm("Sign out all sessions for this account? You will need to sign in again.")) return;
          void api("POST", "/v1/sso/logout-all").then(() => router.push("/login"))
            .catch((err: unknown) => report(err));
        }}>Sign out everywhere</button>
      </section>
    </section>
  );
}

export default function UsersPage() { return <SignedIn>{(s) => <Users s={s} />}</SignedIn>; }
