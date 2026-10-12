"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { MailCheck } from "lucide-react";
import { AuthBrand, AuthFrame } from "@/ui/AuthFrame";

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
        setError("Password reset is temporarily unavailable. Try again later.");
        return;
      }
      setSubmitted(true);
    } catch {
      setError("Password reset is temporarily unavailable. Try again later.");
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthFrame plane="client">
      <AuthBrand plane="client" />
      <div>
        <h1 id="auth-title">Reset your password</h1>
        {!submitted ? <p className="lede">Enter your work email. If it has an account, we send a single-use reset link.</p> : null}
      </div>
      {submitted ? (
        <div className="banner success" role="status" aria-live="polite">
          <MailCheck size={18} aria-hidden="true" />
          <p className="grow">If that address has an active account, a reset link has been sent.</p>
        </div>
      ) : (
        <>
          {error ? <div className="banner error" role="alert"><p className="grow">{error}</p></div> : null}
          <form onSubmit={requestReset}>
            <div className="field">
              <label htmlFor="email">Work email</label>
              <input
                id="email"
                className="input"
                type="email"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                maxLength={320}
                placeholder="name@company.com"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <button className="btn primary lg block" type="submit" disabled={pending}>
              {pending ? "Sending" : "Send reset link"}
            </button>
          </form>
        </>
      )}
      <p className="auth-links"><Link className="link" href="/login">Return to sign in</Link></p>
    </AuthFrame>
  );
}
