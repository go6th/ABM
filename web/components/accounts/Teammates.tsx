"use client";
// Teammates: invite a person by work email and role, and see everyone in the workspace with who
// has not accepted yet. Someone who already has an account is added straight away and signs in
// with their own password; a new person gets an invite link (emailed when mail is set up, and
// shown here to copy). Inviting needs a recent multi-factor sign-in; the server says so.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { TeamInvite, TeamMember } from "@/lib/gen/api.gen";
import { Pill } from "@/ui/design";

const ROLES = [
  ["analyst", "Analyst: researches and asks"],
  ["seller", "Seller: reads accounts and asks"],
  ["viewer", "Viewer: reads only"],
  ["admin", "Admin: also manages the workspace"],
] as const;
const ROLE_NAME: Record<string, string> = { admin: "Admin", analyst: "Analyst", seller: "Seller", viewer: "Viewer" };

function inviteFailure(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 403) return "Sign in again with multi-factor authentication, then invite.";
    if (cause.status === 409 && cause.type.endsWith("invite_pending_elsewhere")) return "This person has an invitation waiting. Once they accept it, invite them again.";
    if (cause.status === 409 && cause.detail === "already_member") return "This person is already in your workspace.";
    if (cause.status === 409) return "This address cannot be invited.";
    if (cause.status === 402) return "Your workspace has no free seats. Ask 6th for more.";
    if (cause.status === 422) return "Enter a full work email, like name@company.com.";
  }
  return "The invitation could not be sent. Try again.";
}

export function Teammates({ members }: { members: TeamMember[] }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<string>(ROLES[0][0]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string; link?: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const invite = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setResult(null);
    setCopied(false);
    try {
      const made = await api<TeamInvite>("POST", "/v1/users/invite", { email: email.trim().toLowerCase(), role });
      if (made.existing_account) {
        setResult({ ok: true, text: `${made.email} is added. They already have an account, so they sign in with their own password.` });
      } else if (made.emailed) {
        setResult({ ok: true, text: `Invitation emailed to ${made.email}. The link works for 24 hours.` });
      } else {
        setResult({ ok: true, text: `Send this link to ${made.email}. It works for 24 hours and only once.`, link: made.invite_url ?? undefined });
      }
      setEmail("");
      router.refresh();
    } catch (cause) {
      setResult({ ok: false, text: inviteFailure(cause) });
    } finally {
      setBusy(false);
    }
  };

  const copy = async (link: string) => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <>
      <form className="card pad stack-14" onSubmit={(e) => void invite(e)} aria-labelledby="invite-title">
        <h2 id="invite-title" className="card-title">Invite a teammate</h2>
        <div className="row-12 wrap align-top">
          <div className="field grow-220"><label htmlFor="mate-email">Work email</label><input id="mate-email" className="input" type="email" required maxLength={320} autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
          <div className="field grow-220"><label htmlFor="mate-role">Role</label><select id="mate-role" className="input" value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
        </div>
        {result ? (
          <div className="stack-8" role={result.ok ? "status" : "alert"}>
            <p className={`sm-text ${result.ok ? "" : "bad-text"}`}>{result.text}</p>
            {result.link ? (
              <div className="row-12 wrap">
                <label htmlFor="mate-link" className="sr-only">Invite link</label>
                <input id="mate-link" className="input grow-220" readOnly value={result.link} onFocus={(e) => e.target.select()} />
                <button type="button" className="btn" onClick={() => void copy(result.link as string)}>{copied ? "Copied" : "Copy link"}</button>
              </div>
            ) : null}
          </div>
        ) : null}
        <button type="submit" className="btn primary align-start" disabled={busy}>{busy ? "Inviting…" : "Invite"}</button>
      </form>
      <section className="card list" aria-labelledby="team-title">
        <h2 id="team-title" className="card-title lrow">People in this workspace</h2>
        {members.length ? members.map((m) => (
          <div key={m.user_id} className="lrow">
            <span className="grow">
              {m.display_name || m.email || m.user_id}
              {m.you ? <span className="mut"> (you)</span> : null}
              {m.display_name && m.email ? <span className="sub mut xs-text">{m.email}</span> : null}
            </span>
            <Pill small>{ROLE_NAME[m.role] ?? m.role}</Pill>
            {m.status !== "active" ? <Pill tone="warn" small>{m.status === "suspended" ? "Suspended" : m.status}</Pill>
              : m.pending ? <Pill tone="warn" small>Invited</Pill>
              : <Pill tone="ok" small>Active</Pill>}
          </div>
        )) : <div className="lrow mut">No one yet.</div>}
      </section>
    </>
  );
}
