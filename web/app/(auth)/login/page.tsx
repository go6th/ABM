"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowRight, Building2, Eye, EyeOff, LockKeyhole, Mail, ShieldCheck, Sparkles } from "lucide-react";
import { clearSession } from "@/lib/api";

const SLUG = /^[a-z0-9][a-z0-9-]{2,62}$/;

export default function TenantLoginPage() {
  const router = useRouter();
  const [organization, setOrganization] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenants, setTenants] = useState<Array<{ tenant_id: string; role: string }>>([]);
  const [tenantId, setTenantId] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authMode, setAuthMode] = useState<"password" | "sso">("password");
  const [showPassword, setShowPassword] = useState(false);
  const slug = organization.trim().toLowerCase();

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      const response = await fetch("/v1/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ email, password, ...(tenantId ? { workspace_id: tenantId } : {}) }),
        cache: "no-store",
      });
      const result = (await response.json()) as {
        code?: string;
        tenants?: Array<{ tenant_id: string; role: string }>;
      };
      const firstTenant = result.tenants?.[0];
      if (response.status === 409 && result.code === "tenant_selection_required" && firstTenant) {
        setTenants(result.tenants ?? [firstTenant]);
        setTenantId(firstTenant.tenant_id);
        return;
      }
      if (!response.ok) {
        setError("Sign-in could not be completed. Check your details or contact your administrator.");
        return;
      }
      clearSession();
      router.replace("/");
    } catch {
      setError("Sign-in is temporarily unavailable. Please try again.");
    } finally {
      setPending(false);
    }
  }

  async function resendVerification() {
    setError(null);
    setPending(true);
    try {
      const response = await fetch("/v1/auth/email/resend", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ email }),
        cache: "no-store",
      });
      if (!response.ok) {
        setError("A verification message could not be sent right now. Please try again later.");
      } else {
        setError("If this address has a pending account, a fresh verification link has been sent.");
      }
    } catch {
      setError("A verification message could not be sent right now. Please try again.");
    } finally {
      setPending(false);
    }
  }

  function continueToOrganization(event: FormEvent<HTMLFormElement>) {
    if (!SLUG.test(slug)) {
      event.preventDefault();
      setError("Enter the organization sign-in ID provided by your administrator.");
    }
  }

  return (
    <main className="login-page">
      <div className="login-layout">
        <aside className="login-intro" aria-label="About Account Intelligence">
        <header className="login-brand">
          <div className="login-logo" aria-hidden="true">
            <span className="login-logo-number">6</span>
            <Sparkles className="login-logo-sparkle" />
          </div>
          <span className="login-wordmark">6th <span>agents</span></span>
          <p className="login-brand-caption">ACCOUNT INTELLIGENCE</p>
        </header>

        <div className="login-intro-copy">
          <p className="login-intro-kicker">RESEARCH WITH CONFIDENCE</p>
          <h2>Clear signals.<br />Confident decisions.</h2>
          <p>
            Bring account research, evidence, and important changes together in one
            secure workspace.
          </p>
        </div>

        <ul className="login-highlights">
          <li>
            <span className="login-highlight-icon lavender"><Building2 aria-hidden="true" /></span>
            <span><strong>Account research</strong><small>Keep company intelligence organised</small></span>
          </li>
          <li>
            <span className="login-highlight-icon mint"><ShieldCheck aria-hidden="true" /></span>
            <span><strong>Evidence you can review</strong><small>See the sources behind findings</small></span>
          </li>
          <li>
            <span className="login-highlight-icon peach"><Sparkles aria-hidden="true" /></span>
            <span><strong>Signals that matter</strong><small>Stay aware of meaningful changes</small></span>
          </li>
        </ul>
        <p className="login-intro-foot">A focused workspace for better account decisions.</p>
        </aside>

        <section className="login-card" aria-labelledby="login-title">
        <p className="login-card-kicker">SECURE WORKSPACE ACCESS</p>
        <div className="login-heading">
          <h1 id="login-title">Welcome back</h1>
          <p>Sign in to continue to your workspace.</p>
        </div>

        <div className="login-mode-switch" role="tablist" aria-label="Sign-in method">
          <button
            type="button"
            role="tab"
            id="password-tab"
            aria-selected={authMode === "password"}
            aria-controls="login-panel"
            className={authMode === "password" ? "is-active" : ""}
            onClick={() => { setAuthMode("password"); setError(null); }}
          >
            <Mail aria-hidden="true" /> Email &amp; password
          </button>
          <button
            type="button"
            role="tab"
            id="sso-tab"
            aria-selected={authMode === "sso"}
            aria-controls="login-panel"
            className={authMode === "sso" ? "is-active" : ""}
            onClick={() => { setAuthMode("sso"); setError(null); }}
          >
            <Building2 aria-hidden="true" /> Enterprise SSO
          </button>
        </div>

        <div id="login-panel" role="tabpanel" aria-labelledby={authMode === "password" ? "password-tab" : "sso-tab"}>
          {error ? (
            <div className="login-message" role="alert">
              <p>{error}</p>
            </div>
          ) : null}

          {authMode === "password" ? (
            <form className="login-form" onSubmit={signIn}>
              <div className="login-field">
                <label htmlFor="email">Work email</label>
                <div className="login-input-wrap">
                  <Mail aria-hidden="true" />
                  <input
                    id="email"
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
              </div>

              <div className="login-field">
                <div className="login-label-row">
                  <label htmlFor="password">Password</label>
                  <Link href="/forgot">Forgot password?</Link>
                </div>
                <div className="login-input-wrap">
                  <LockKeyhole aria-hidden="true" />
                  <input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    required
                    maxLength={1024}
                    placeholder="Enter your password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  <button
                    type="button"
                    className="login-password-toggle"
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    onClick={() => setShowPassword((previous) => !previous)}
                  >
                    {showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                  </button>
                </div>
              </div>

              {tenants.length > 0 ? (
                <div className="login-field">
                  <label htmlFor="tenant">Select workspace</label>
                  <select id="tenant" value={tenantId} onChange={(event) => setTenantId(event.target.value)}>
                    {tenants.map((tenant) => (
                      <option key={tenant.tenant_id} value={tenant.tenant_id}>
                        {tenant.tenant_id} ({tenant.role})
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}

              <button className="login-submit" type="submit" disabled={pending}>
                {pending ? "Signing in…" : "Sign in to workspace"}
                {!pending ? <ArrowRight aria-hidden="true" /> : null}
              </button>

              <div className="login-utility-links">
                <button type="button" disabled={pending || !email} onClick={() => void resendVerification()}>
                  Resend verification email
                </button>
                <Link href="/verify-email">Verify email</Link>
              </div>
            </form>
          ) : (
            <form
              className="login-form"
              action={`/v1/sso/${encodeURIComponent(slug)}/start?return_to=%2F`}
              method="get"
              onSubmit={continueToOrganization}
            >
              <div className="login-field">
                <label htmlFor="organization">Organization sign-in ID</label>
                <div className="login-input-wrap">
                  <Building2 aria-hidden="true" />
                  <input
                    id="organization"
                    autoComplete="organization"
                    autoCapitalize="none"
                    spellCheck={false}
                    required
                    maxLength={63}
                    placeholder="e.g. acme-corp"
                    value={organization}
                    onChange={(event) => {
                      setOrganization(event.target.value);
                      setError(null);
                    }}
                  />
                </div>
                <p className="login-field-hint">Use the sign-in ID provided by your IT administrator.</p>
              </div>
              <button className="login-submit" type="submit">
                Continue with SSO <ArrowRight aria-hidden="true" />
              </button>
            </form>
          )}
        </div>

        <footer className="login-footer">
          <p><ShieldCheck aria-hidden="true" /> Your workspace data is protected with secure sign-in.</p>
          <span>Need access? Contact your workspace administrator.</span>
        </footer>
        </section>
      </div>
    </main>
  );
}
