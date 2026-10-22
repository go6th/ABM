"use client";
// Accept an invite: the link an operator copied in Add client carries a single-use token in its
// fragment (never sent to a server log); the invited person chooses a password and then signs in.

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { AuthFrame } from "@/ui/AuthFrame";
import { ApiError, apiPublic } from "@/lib/api";
import type { PasswordResetCompleted } from "@/lib/gen/api.gen";

export default function AcceptInvitePage() {
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    const secret = window.location.hash.slice(1);
    window.history.replaceState(window.history.state, "", window.location.pathname);
    void Promise.resolve().then(() => { setToken(secret); setChecked(true); });
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!token) {
      setError("This invite link is incomplete or expired. Ask 6th for a new one.");
      return;
    }
    if (password !== confirm) {
      setError("The passwords do not match.");
      return;
    }
    setPending(true);
    try {
      await apiPublic<PasswordResetCompleted>("POST", "/v1/auth/invite/accept", { token, password });
      setComplete(true);
      setToken("");
      setPassword("");
      setConfirm("");
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        setError(e.status === 422
          ? "Choose a password of at least 12 characters that does not contain your email or name."
          : "This invite link was already used or has expired. Ask 6th for a new one.");
      } else {
        setError("Joining is temporarily unavailable. Try again.");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthFrame plane="client">
      <div>
        <h1 id="auth-title">Join your workspace</h1>
        {!complete ? <p className="sub">Choose a password to finish joining. Use at least 12 characters and leave out your email and name.</p> : null}
      </div>
      {complete ? (
        <div className="notice" role="status" aria-live="polite">
          <p>You have joined. <Link href="/login">Sign in</Link> with your email and this password.</p>
        </div>
      ) : (
        <>
          {checked && !token ? (
            <div className="notice" role="status">
              <p>This invite link is incomplete or expired. Ask 6th for a new one.</p>
            </div>
          ) : null}
          {error ? <div className="notice error" role="alert"><p>{error}</p></div> : null}
          <form onSubmit={submit}>
            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                className="input"
                type="password"
                autoComplete="new-password"
                minLength={12}
                maxLength={128}
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="confirm">Confirm password</label>
              <input
                id="confirm"
                className="input"
                type="password"
                autoComplete="new-password"
                minLength={12}
                maxLength={128}
                required
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
              />
            </div>
            <button className="btn primary block" type="submit" disabled={pending || !token}>
              {pending ? "Joining" : "Join"}
            </button>
          </form>
        </>
      )}
      <p className="auth-links"><Link href="/login">Return to sign in</Link></p>
    </AuthFrame>
  );
}
