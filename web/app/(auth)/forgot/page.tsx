"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function requestReset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/v1/auth/password/reset/request", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ email }),
        cache: "no-store",
      });
      if (!response.ok) {
        setError("Password reset is temporarily unavailable. Please try again later.");
        return;
      }
      setSubmitted(true);
    } catch {
      setError("Password reset is temporarily unavailable. Please try again later.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="auth-frame">
      <section className="auth-card surface" aria-labelledby="forgot-title">
        <p className="eyebrow">Account Intelligence</p>
        <h1 id="forgot-title">Reset your password</h1>
        {submitted ? (
          <p role="status" aria-live="polite">
            If that address has an active account, a reset link has been sent.
          </p>
        ) : (
          <>
            <p className="muted">Enter your account email and we’ll send a single-use reset link if eligible.</p>
            {error ? <p className="error" role="alert">{error}</p> : null}
            <form onSubmit={requestReset}>
              <label htmlFor="email">Work email</label>
              <input
                id="email"
                type="email"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                maxLength={320}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              <button className="button primary" type="submit" disabled={pending}>
                {pending ? "Sending…" : "Send reset link"}
              </button>
            </form>
          </>
        )}
        <p className="small"><Link href="/login">Return to sign in</Link></p>
      </section>
    </main>
  );
}
