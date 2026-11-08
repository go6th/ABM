"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AuthFrame } from "@/ui/AuthFrame";
import { ApiError, apiPublic } from "@/lib/api";
import type { EmailVerified } from "@/lib/gen/api.gen";

// Opens from the link in the verification email; the link works once and for a limited time.
// A link that is incomplete or no longer valid points to the sign-in page, which offers a new
// link when the email is still waiting to be verified, or to the administrator.
const NEW_LINK = "Sign in with your email and password: if it is still waiting to be verified, the sign-in page offers a new link. Otherwise ask your administrator.";

export default function VerifyEmailPage() {
  const [status, setStatus] = useState("Checking your verification link");
  const [finished, setFinished] = useState(false);
  const [ok, setOk] = useState(false);

  useEffect(() => {
    const token = window.location.hash.slice(1);
    window.history.replaceState(window.history.state, "", window.location.pathname);
    if (!token) {
      void Promise.resolve().then(() => {
        setStatus(`This verification link is incomplete or expired. ${NEW_LINK}`);
        setFinished(true);
      });
      return;
    }

    void apiPublic<EmailVerified>("POST", "/v1/auth/email/verify", { token })
      .then(() => {
        setOk(true);
        setStatus("Your email is verified. You can now sign in with your password.");
      })
      .catch((e: unknown) => setStatus(e instanceof ApiError && e.status >= 400 && e.status < 500
        ? `This verification link is invalid or expired. ${NEW_LINK}`
        : "Email verification is temporarily unavailable. Try again."))
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
