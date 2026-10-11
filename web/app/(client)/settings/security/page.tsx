"use client";
// Security and sign in: add password sign-in, and the caller's active sessions with revoke.
import { useState } from "react";
import { Laptop, ShieldCheck } from "lucide-react";
import { SignedIn } from "@/ui/SignedIn";
import { Badge, LoadError, PageHead, Section } from "@/ui/primitives";
import { DataTable, type DataColumn } from "@/ui/DataTable";
import { api, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { SessionList } from "@/lib/gen/api.gen";

type Row = SessionList["sessions"][number];

function when(iso: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}

function SecuritySessions() {
  const loaded = useApi<SessionList>("/v1/sso/sessions");
  const [password, setPassword] = useState("");
  const [passwordAgain, setPasswordAgain] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function revoke(sessionId: string) {
    setBusy(sessionId);
    setMessage(null);
    try {
      await api<void>("DELETE", `/v1/sso/sessions/${encodeURIComponent(sessionId)}`);
      setMessage({ ok: true, text: "Session revoked." });
      loaded.reload();
    } catch (error) {
      setMessage({ ok: false, text: error instanceof ApiError ? `Could not revoke session (${error.type}).` : "Could not revoke session." });
    } finally {
      setBusy(null);
    }
  }

  async function enrollPassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    if (password !== passwordAgain) {
      setMessage({ ok: false, text: "The passwords do not match." });
      return;
    }
    setBusy("password");
    try {
      await api("POST", "/v1/auth/password/enroll", { password });
      setPassword("");
      setPasswordAgain("");
      setMessage({ ok: true, text: "If verification email is configured, a one-time link has been sent. Verify your address before using password sign-in." });
    } catch (error) {
      setMessage({ ok: false, text: error instanceof ApiError ? `Password enrollment could not be completed (${error.type}).` : "Password enrollment could not be completed." });
    } finally {
      setBusy(null);
    }
  }

  const columns: DataColumn<Row>[] = [
    {
      key: "device",
      header: "Sign-in",
      render: (session) => (
        <span className="row nowrap">
          <Laptop size={18} strokeWidth={1.7} aria-hidden="true" className="ink-2" />
          <span>{session.current ? <strong>This device</strong> : "Other device"}<br /><span className="t-footnote ink-2">{when(session.created_at)}</span></span>
        </span>
      ),
    },
    { key: "seen", header: "Last active", render: (session) => when(session.last_seen_at) },
    { key: "expires", header: "Expires", render: (session) => when(session.expires_at) },
    { key: "amr", header: "Authentication", render: (session) => (session.amr.length ? session.amr.join(" and ") : "Organization sign-in") },
    {
      key: "action",
      header: "Action",
      render: (session) => session.current ? <Badge tone="mint" dot><span aria-label="Current session">Current</span></Badge> : (
        <button type="button" className="btn sm" disabled={busy !== null} onClick={() => void revoke(session.session_id)}>
          {busy === session.session_id ? "Revoking" : "Revoke"}
        </button>
      ),
    },
  ];

  return (
    <div className="content">
      <PageHead title="Security and sign in" sub="Review where you are signed in and revoke sessions you do not recognize." />
      {message ? <p className={message.ok ? "inline-status block-gap" : "inline-error block-gap"} role="status">{message.text}</p> : null}
      <Section title="Password sign-in" id="password-enroll" sub="Add password sign-in to your verified organization account. The password is stored only as an Argon2id hash.">
        <form className="card form" onSubmit={(event) => void enrollPassword(event)}>
          <div className="grid-2">
            <div className="field">
              <label htmlFor="new-password">New password</label>
              <input id="new-password" className="input" type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={password} onChange={(event) => setPassword(event.target.value)} aria-describedby="new-password-hint" />
              <p className="hint" id="new-password-hint">At least 12 characters.</p>
            </div>
            <div className="field">
              <label htmlFor="new-password-again">Confirm password</label>
              <input id="new-password-again" className="input" type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={passwordAgain} onChange={(event) => setPasswordAgain(event.target.value)} />
            </div>
          </div>
          <div><button type="submit" className="btn primary" disabled={busy !== null}><ShieldCheck size={16} aria-hidden="true" />{busy === "password" ? "Sending verification" : "Add password sign-in"}</button></div>
        </form>
      </Section>
      <Section title="Active sessions">
        {loaded.error ? <LoadError reference={null} retryHref="/settings/security" /> : (
          <DataTable caption="Active sessions" columns={columns} rows={loaded.data?.sessions ?? []} rowKey={(session) => session.session_id} loading={!loaded.data} emptyMessage="No active sessions were found." />
        )}
      </Section>
    </div>
  );
}

export default function SecurityPage() {
  return <SignedIn>{() => <SecuritySessions />}</SignedIn>;
}
