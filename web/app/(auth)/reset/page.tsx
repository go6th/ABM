"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";

export default function ResetPasswordPage() {
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const secret = window.location.hash.slice(1);
    window.history.replaceState(window.history.state, "", window.location.pathname);
    void Promise.resolve().then(() => setToken(secret));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!token) {
      setError("This reset link is incomplete or expired. Request a new one.");
      return;
    }
    if (password !== confirm) {
      setError("The passwords do not match.");
      return;
    }
    setPending(true);
    try {
      const response = await fetch("/v1/auth/password/reset", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ token, password }),
        cache: "no-store",
      });
      if (!response.ok) {
        setError(response.status === 422
          ? "Choose a password of at least 12 characters that does not contain your email or name."
          : "This reset link is invalid or expired. Request a new one.");
        return;
      }
      setComplete(true);
      setToken("");
      setPassword("");
      setConfirm("");
    } catch {
      setError("Password reset is temporarily unavailable. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="auth-frame">
      <section className="auth-card surface" aria-labelledby="reset-title">
        <p className="eyebrow">Account Intelligence</p>
        <h1 id="reset-title">Choose a new password</h1>
        {complete ? (
          <p role="status" aria-live="polite">Password updated. Other active sessions have been signed out.</p>
        ) : (
          <>
            <p className="muted">Use at least 12 characters. Don’t include your email or name.</p>
            {error ? <p className="error" role="alert">{error}</p> : null}
            <form onSubmit={submit}>
              <label htmlFor="password">New password</label>
              <input
                id="password"
                type="password"
                autoComplete="new-password"
                minLength={12}
                maxLength={128}
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              <label htmlFor="confirm">Confirm new password</label>
              <input
                id="confirm"
                type="password"
                autoComplete="new-password"
                minLength={12}
                maxLength={128}
                required
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
              />
              <button className="button primary" type="submit" disabled={pending || !token}>
                {pending ? "Updating…" : "Update password"}
              </button>
            </form>
          </>
        )}
        <p className="small"><Link href="/login">Return to sign in</Link></p>
      </section>
    </main>
  );
}
