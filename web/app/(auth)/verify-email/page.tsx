"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

export default function VerifyEmailPage() {
  const [status, setStatus] = useState("Checking your verification link…");
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    const token = window.location.hash.slice(1);
    window.history.replaceState(window.history.state, "", window.location.pathname);
    if (!token) {
      void Promise.resolve().then(() => {
        setStatus("This verification link is incomplete or expired. Request a new link from Security settings.");
        setFinished(true);
      });
      return;
    }

    void fetch("/v1/auth/email/verify", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ token }),
      cache: "no-store",
    })
      .then((response) => {
        setStatus(response.ok ? "Your email is verified. You can now sign in with your password." : "This verification link is invalid or expired. Request a new link from Security settings.");
      })
      .catch(() => setStatus("Email verification is temporarily unavailable. Please try again."))
      .finally(() => setFinished(true));
  }, []);

  return (
    <main className="auth-frame">
      <section className="auth-card surface" aria-labelledby="verify-title">
        <p className="eyebrow">Account Intelligence</p>
        <h1 id="verify-title">Email verification</h1>
        <p role="status" aria-live="polite">{status}</p>
        {finished ? <Link className="button primary" href="/login">Continue to sign in</Link> : null}
      </section>
    </main>
  );
}
