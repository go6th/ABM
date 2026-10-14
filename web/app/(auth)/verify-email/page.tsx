"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AuthFrame } from "@/ui/AuthFrame";

export default function VerifyEmailPage() {
  const [status, setStatus] = useState("Checking your verification link");
  const [finished, setFinished] = useState(false);
  const [ok, setOk] = useState(false);

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
        setOk(response.ok);
        setStatus(response.ok ? "Your email is verified. You can now sign in with your password." : "This verification link is invalid or expired. Request a new link from Security settings.");
      })
      .catch(() => setStatus("Email verification is temporarily unavailable. Try again."))
      .finally(() => setFinished(true));
  }, []);

  return (
    <AuthFrame plane="client">
      <h1 id="auth-title">Email verification</h1>
      <div className={finished && !ok ? "notice error" : "notice"} role="status" aria-live="polite">
        {!finished ? <span className="spinner" aria-hidden="true" /> : null}
        <p>{status}</p>
      </div>
      {finished ? <Link className="btn primary block" href="/login">Continue to sign in</Link> : null}
    </AuthFrame>
  );
}
