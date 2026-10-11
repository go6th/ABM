"use client";
// Members and roles (admins only; changes need a fresh multi-factor sign-in).
import { useState } from "react";
import { useRouter } from "next/navigation";
import { UserPlus } from "lucide-react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { humanize } from "@/lib/ws-format";
import { DataTable, type DataColumn } from "@/ui/DataTable";
import { ConfirmButton } from "@/ui/ConfirmButton";
import { Badge, Banner, EmptyState, LoadError, PageHead, Section } from "@/ui/primitives";

interface Member { user_id: string; role: string; status: string; version: number }
const ROLES = ["admin", "analyst", "seller", "viewer"];

function Users({ s }: { s: Session }) {
  const router = useRouter();
  const list = useApi<{ members: Member[] }>(s.role === "admin" ? "/v1/users" : null);
  const members = list.data?.members ?? [];
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [uid, setUid] = useState(""); const [role, setRole] = useState("viewer");
  const load = list.reload;
  if (s.role !== "admin") return <div className="content"><PageHead title="Team and access" /><Banner kind="neutral">Only admins manage users.</Banner></div>;
  const report = (err: unknown) => setMsg({ ok: false, text: err instanceof ApiError ? (err.type === "step_up_required" ? "Sign in again with multi-factor authentication to change members." : `Refused (${err.type}).`) : "Refused." });
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    try { await api("POST", "/v1/users", { user_id: uid.trim(), role }); setUid(""); setMsg({ ok: true, text: "Member added." }); load(); }
    catch (err) { report(err); }
  };
  const change = async (m: Member, patch: { role?: string; status?: string }) => {
    setMsg(null);
    try { await api("PATCH", "/v1/users", { user_id: m.user_id, expected_version: m.version, ...patch }); setMsg({ ok: true, text: "Member updated." }); load(); }
    catch (err) { report(err); }
  };
  const columns: DataColumn<Member>[] = [
    { key: "identity", header: "Member", render: (member) => <span className="mono t-footnote">{cellText(member.user_id)}</span> },
    {
      key: "role",
      header: "Role",
      render: (member) => (
        <select aria-label={`Role of ${member.user_id}`} className="select" value={member.role} disabled={member.user_id === s.user_id}
          onChange={(event) => void change(member, { role: event.target.value })}>
          {ROLES.map((entry) => <option key={entry} value={entry}>{humanize(entry)}</option>)}
        </select>
      ),
    },
    { key: "status", header: "Status", render: (member) => <Badge tone={member.status === "active" ? "mint" : "butter"} dot>{humanize(member.status)}</Badge> },
    {
      key: "actions",
      header: "Actions",
      render: (member) => member.user_id === s.user_id ? <span className="ink-2">You</span> : (
        <div className="row nowrap">
          {member.status === "active"
            ? <button className="btn sm" type="button" onClick={() => void change(member, { status: "suspended" })}>Suspend</button>
            : <button className="btn sm" type="button" onClick={() => void change(member, { status: "active" })}>Reactivate</button>}
          <ConfirmButton label="Remove" size="sm" tone="danger" title="Remove this member?" body={`${member.user_id} loses access to this workspace.`} action="Remove" onConfirm={() => void change(member, { status: "removed" })} />
        </div>
      ),
    },
  ];
  return (
    <div className="content">
      <PageHead title="Team and access" sub="Who can use this workspace and with which role. Changes need a recent multi-factor sign-in." />
      {msg ? <p className={msg.ok ? "inline-status block-gap" : "inline-error block-gap"} role={msg.ok ? "status" : "alert"}>{msg.text}</p> : null}
      <Section title="Add a member">
        <form className="card form-row" onSubmit={(e) => void add(e)} aria-label="Add a member">
          <div className="field">
            <label htmlFor="uid">Sign-in identity</label>
            <input className="input mono" id="uid" value={uid} onChange={(e) => setUid(e.target.value)} placeholder="connection:subject" required maxLength={200} />
          </div>
          <div className="field">
            <label htmlFor="role">Workspace role</label>
            <select id="role" className="select" value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}</select>
          </div>
          <button className="btn primary" type="submit"><UserPlus size={16} aria-hidden="true" />Add member</button>
        </form>
      </Section>
      <Section title="Members">
        {list.error ? <LoadError reference={null} retryHref="/settings/users" />
          : list.data && members.length === 0 ? <EmptyState title="No members returned" body="The workspace returned no member records." />
            : <DataTable caption="Workspace team members" columns={columns} rows={members} rowKey={(member) => member.user_id} loading={!list.data} />}
      </Section>
      <Section title="Sign out everywhere" sub="End every active session for your account, on every device.">
        <ConfirmButton label="Sign out everywhere" tone="danger" title="Sign out everywhere?" body="Every session for your account ends. You will need to sign in again." action="Sign out everywhere"
          onConfirm={() => { void api("POST", "/v1/sso/logout-all").then(() => router.push("/login")).catch((err: unknown) => report(err)); }} />
      </Section>
    </div>
  );
}

export default function UsersPage() { return <SignedIn>{(s) => <Users s={s} />}</SignedIn>; }
