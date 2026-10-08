"use client";
// Members and roles (admins only; changes need a fresh multi-factor sign-in).
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";

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
  const add = async (e: React.FormEvent) => { e.preventDefault(); try { await api("POST", "/v1/users", { user_id: uid.trim(), role }); setUid(""); load(); } catch (err) { report(err); } };
  const change = async (m: Member, patch: { role?: string; status?: string }) => { try { await api("PATCH", "/v1/users", { user_id: m.user_id, expected_version: m.version, ...patch }); load(); } catch (err) { report(err); } };
  return (
    <section>
      <h1>Users</h1>
      {msg ? <p role="alert">{msg}</p> : null}
      <form className="row" onSubmit={add} aria-label="Add a member">
        <label htmlFor="uid">Sign-in identity</label><input id="uid" value={uid} onChange={(e) => setUid(e.target.value)} placeholder="connection:subject" />
        <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select>
        <button className="primary" type="submit">Add</button>
      </form>
      <table className="data"><thead><tr><th scope="col">Member</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
        <tbody>{members.map((m) => (
          <tr key={m.user_id}><td>{cellText(m.user_id)}</td>
            <td><select aria-label={`Role of ${m.user_id}`} value={m.role} disabled={m.user_id === s.user_id} onChange={(e) => void change(m, { role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></td>
            <td>{m.status}</td>
            <td>{m.user_id === s.user_id ? <span className="muted">You</span> : (
              <>{m.status === "active" ? <button type="button" onClick={() => void change(m, { status: "suspended" })}>Suspend</button> : <button type="button" onClick={() => void change(m, { status: "active" })}>Reactivate</button>}
                <button type="button" onClick={() => void change(m, { status: "removed" })}>Remove</button></>
            )}</td></tr>
        ))}</tbody></table>
      <button type="button" onClick={() => void api("POST", "/v1/sso/logout-all").then(() => router.push("/login"))}>Sign out everywhere</button>
    </section>
  );
}

export default function UsersPage() { return <SignedIn>{(s) => <Users s={s} />}</SignedIn>; }
