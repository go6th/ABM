"use client";

import { useState } from "react";
import { SignedIn } from "@/components/SignedIn";
import { api, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { SessionList } from "@/lib/gen/api.gen";

function SecuritySessions() {
  const loaded = useApi<SessionList>("/v1/sso/sessions");
  const [password, setPassword] = useState("");
  const [passwordAgain, setPasswordAgain] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function revoke(sessionId: string) {
    setBusy(sessionId);
    setMessage(null);
    try {
      await api<void>("DELETE", `/v1/sso/sessions/${encodeURIComponent(sessionId)}`);
      setMessage("Session revoked.");
      loaded.reload();
    } catch (error) {
      setMessage(error instanceof ApiError ? `Could not revoke session (${error.type}).` : "Could not revoke session.");
    } finally {
      setBusy(null);
    }
  }

  async function enrollPassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    if (password !== passwordAgain) {
      setMessage("The passwords do not match.");
      return;
    }
    setBusy("password");
    try {
      await api("POST", "/v1/auth/password/enroll", { password });
      setPassword("");
      setPasswordAgain("");
      setMessage("If verification email is configured, a one-time link has been sent. Verify your address before using password sign-in.");
    } catch (error) {
      setMessage(error instanceof ApiError ? `Password enrollment could not be completed (${error.type}).` : "Password enrollment could not be completed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section>
      <h1>Security</h1>
      <p className="muted">Review active sign-ins for this workspace and revoke sessions you do not recognize.</p>
      <section aria-labelledby="password-enroll-title">
        <h2 id="password-enroll-title">Password sign-in</h2>
        <p className="muted">Add password sign-in to your verified organization account. Your password stays in this request and is stored only as an Argon2id hash.</p>
        <form onSubmit={(event) => void enrollPassword(event)}>
          <label htmlFor="new-password">New password</label>
          <input id="new-password" type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={password} onChange={(event) => setPassword(event.target.value)} />
          <label htmlFor="new-password-again">Confirm password</label>
          <input id="new-password-again" type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={passwordAgain} onChange={(event) => setPasswordAgain(event.target.value)} />
          <button type="submit" disabled={busy !== null}>{busy === "password" ? "Sending verification…" : "Add password sign-in"}</button>
        </form>
      </section>
      {message ? <p role="status">{message}</p> : null}
      {loaded.error ? (
        <div role="alert" className="error">
          <p>Active sessions could not be loaded.</p>
          <button type="button" onClick={loaded.reload}>Try again</button>
        </div>
      ) : null}
      {!loaded.data && !loaded.error ? <p className="muted" role="status">Loading active sessions…</p> : null}
      {loaded.data?.sessions.length === 0 ? <p>No active sessions were found.</p> : null}
      {loaded.data?.sessions.length ? (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th scope="col">Sign-in</th><th scope="col">Last active</th><th scope="col">Expires</th><th scope="col">Authentication</th><th scope="col">Action</th></tr></thead>
            <tbody>
              {loaded.data.sessions.map((session) => (
                <tr key={session.session_id}>
                  <td>{session.current ? <strong>This device</strong> : "Other device"}<br /><small>{new Date(session.created_at).toLocaleString()}</small></td>
                  <td>{new Date(session.last_seen_at).toLocaleString()}</td>
                  <td>{new Date(session.expires_at).toLocaleString()}</td>
                  <td>{session.amr.length ? session.amr.join(" + ") : "Organization sign-in"}</td>
                  <td>{session.current ? <span aria-label="Current session">Current</span> : (
                    <button type="button" disabled={busy !== null} onClick={() => void revoke(session.session_id)}>
                      {busy === session.session_id ? "Revoking…" : "Revoke"}
                    </button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

export default function SecurityPage() {
  return <SignedIn>{() => <SecuritySessions />}</SignedIn>;
}
