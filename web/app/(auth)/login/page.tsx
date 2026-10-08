"use client";

import { ArrowRight, Building2, LockKeyhole } from "lucide-react";
import { useState } from "react";

const SLUG = /^[a-z0-9][a-z0-9-]{2,62}$/;

export default function Login() {
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<string | null>(null);

  const go = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const workspace = slug.trim().toLowerCase();
    if (!SLUG.test(workspace)) {
      setError("Enter a workspace name between 3 and 63 letters, numbers, or hyphens.");
      return;
    }

    const candidate = new URLSearchParams(window.location.search).get("return_to") ?? "/";
    const returnTo = candidate.startsWith("/") && !candidate.startsWith("//") && !candidate.includes("\\")
      ? candidate
      : "/";
    const destination = new URL(`/v1/sso/${workspace}/start`, window.location.origin);
    destination.searchParams.set("return_to", returnTo);
    window.location.assign(destination.toString());
  };

  return (
    <section className="client-login">
      <span className="login-emblem" aria-hidden="true"><Building2 /></span>
      <div className="login-heading">
        <p className="eyebrow">WORKSPACE SIGN IN</p>
        <h1>Welcome back</h1>
        <p className="secondary">Sign in to continue to your account intelligence workspace.</p>
      </div>

      <form className="login-form" onSubmit={go} noValidate>
        <div className="field">
          <label htmlFor="workspace">Workspace name</label>
          <input
            className="input"
            id="workspace"
            name="workspace"
            autoComplete="organization"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={63}
            placeholder="your-company"
            value={slug}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? "workspace-error" : "workspace-hint"}
            onChange={(event) => {
              setSlug(event.target.value);
              if (error) setError(null);
            }}
          />
          {error ? (
            <span className="field-error" id="workspace-error" role="alert">{error}</span>
          ) : (
            <span className="hint" id="workspace-hint">Use the sign-in name provided by your organization.</span>
          )}
        </div>
        <button className="btn primary lg auth-submit" type="submit">
          Continue <ArrowRight aria-hidden="true" />
        </button>
      </form>

      <p className="login-security-note"><LockKeyhole aria-hidden="true" /> Your organization’s secure sign-in provider will open next.</p>
    </section>
  );
}
