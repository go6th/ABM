"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { AuthBrand, AuthFrame } from "@/ui/AuthFrame";
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
  const [notice, setNotice] = useState(false);
  const [authMode, setAuthMode] = useState<"password" | "sso">("password");
  const [showPassword, setShowPassword] = useState(false);
  const slug = organization.trim().toLowerCase();

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(false);
    setPending(true);
    try {
      const response = await fetch("/v1/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ email, password, ...(tenantId ? { workspace_id: tenantId } : {}) }),
        cache: "no-store",
      });
      const result = (await response.json()) as { code?: string; tenants?: Array<{ tenant_id: string; role: string }> };
      const firstTenant = result.tenants?.[0];
      if (response.status === 409 && result.code === "tenant_selection_required" && firstTenant) {
        setTenants(result.tenants ?? [firstTenant]);
        setTenantId(firstTenant.tenant_id);
        return;
      }
      if (!response.ok) {
        setError("Sign in could not be completed. Check your details or contact your administrator.");
        return;
      }
      clearSession();
      router.replace("/");
    } catch {
      setError("Sign in is unavailable right now. Try again in a moment.");
    } finally {
      setPending(false);
    }
  }

  async function resendVerification() {
    setError(null);
    setNotice(false);
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
        setError("A verification message could not be sent right now. Try again later.");
      } else {
        setNotice(true);
        setError("If this address has a pending account, a fresh verification link has been sent.");
      }
    } catch {
      setError("A verification message could not be sent right now. Try again.");
    } finally {
      setPending(false);
    }
  }

  function continueToOrganization(event: FormEvent<HTMLFormElement>) {
    if (!SLUG.test(slug)) {
      event.preventDefault();
      setNotice(false);
      setError("Enter the organization sign in ID provided by your administrator.");
    }
  }

  return (
    <AuthFrame plane="client">
      <AuthBrand plane="client" />
      <div>
        <h1 id="auth-title">Sign in</h1>
        <p className="muted">Continue to your workspace.</p>
      </div>

      <div className="seg" role="tablist" aria-label="Sign in method">
        <button type="button" role="tab" id="password-tab" aria-selected={authMode === "password"} aria-controls="login-panel" onClick={() => { setAuthMode("password"); setError(null); }}>
          Email and password
        </button>
        <button type="button" role="tab" id="sso-tab" aria-selected={authMode === "sso"} aria-controls="login-panel" onClick={() => { setAuthMode("sso"); setError(null); }}>
          Enterprise SSO
        </button>
      </div>

      <div id="login-panel" role="tabpanel" aria-labelledby={authMode === "password" ? "password-tab" : "sso-tab"} className="stack">
        {error ? <div className={notice ? "notice" : "notice error"} role="alert"><p>{error}</p></div> : null}

        {authMode === "password" ? (
          <form onSubmit={signIn}>
            <div className="field">
              <label htmlFor="email">Work email</label>
              <input id="email" className="input" type="email" autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={320} placeholder="name@company.com" value={email} onChange={(event) => setEmail(event.target.value)} />
            </div>
            <div className="field">
              <div className="label-row">
                <label htmlFor="password">Password</label>
                <Link href="/forgot">Forgot password?</Link>
              </div>
              <input id="password" className="input" type={showPassword ? "text" : "password"} autoComplete="current-password" required maxLength={1024} value={password} onChange={(event) => setPassword(event.target.value)} />
              <label className="check small"><input type="checkbox" checked={showPassword} onChange={(e) => setShowPassword(e.target.checked)} />Show password</label>
            </div>
            {tenants.length > 0 ? (
              <div className="field">
                <label htmlFor="tenant">Select workspace</label>
                <select id="tenant" className="select" value={tenantId} onChange={(event) => setTenantId(event.target.value)}>
                  {tenants.map((tenant) => <option key={tenant.tenant_id} value={tenant.tenant_id}>{tenant.tenant_id} ({tenant.role})</option>)}
                </select>
              </div>
            ) : null}
            <button className="btn primary block" type="submit" disabled={pending}>{pending ? "Signing in" : "Sign in to workspace"}</button>
            <div className="auth-links">
              <button type="button" disabled={pending || !email} onClick={() => void resendVerification()}>Resend verification email</button>
              <Link href="/verify-email">Verify email</Link>
            </div>
          </form>
        ) : (
          <form action={`/v1/sso/${encodeURIComponent(slug)}/start?return_to=%2F`} method="get" onSubmit={continueToOrganization}>
            <div className="field">
              <label htmlFor="organization">Organization sign in ID</label>
              <input id="organization" className="input mono" autoComplete="organization" autoCapitalize="none" spellCheck={false} required maxLength={63} placeholder="acme-corp" aria-describedby="organization-hint" value={organization} onChange={(event) => { setOrganization(event.target.value); setError(null); }} />
              <p className="hint" id="organization-hint">Use the sign in ID from your IT administrator.</p>
            </div>
            <button className="btn primary block" type="submit">Continue with SSO</button>
          </form>
        )}
      </div>

      <p className="auth-foot">Need access? Ask your workspace administrator.</p>
    </AuthFrame>
  );
}
