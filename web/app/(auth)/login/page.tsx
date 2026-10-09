"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Building2,
  LockKeyhole,
  Lock,
  Mail,
  Eye,
  EyeOff,
  Sparkles,
  AlertCircle,
  KeyRound,
  ShieldCheck,
} from "lucide-react";

const SLUG_REGEX = /^[a-z0-9][a-z0-9-]{2,62}$/;

export default function Login() {
  const router = useRouter();
  const [tab, setTab] = useState<"credentials" | "sso">("credentials");

  // Credentials tab state
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true);

  // SSO tab state
  const [slug, setSlug] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fillDemo = () => {
    setEmail("doctor@synthetic.local");
    setPassword("Workspace2026!");
    setError(null);
  };

  const handleCredentialsSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanEmail = email.trim();
    if (!cleanEmail) {
      setError("Please enter your work email.");
      return;
    }
    if (!password) {
      setError("Please enter your password.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: cleanEmail, password, remember }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        setError(data.detail ?? "Invalid credentials. Please verify your email and password.");
        setSubmitting(false);
        return;
      }

      if (data.access_token && typeof window !== "undefined") {
        window.localStorage.setItem("ai_token", data.access_token);
      }

      router.push("/");
      router.refresh();
    } catch {
      setError("Unable to connect to authentication server. Please try again.");
      setSubmitting(false);
    }
  };

  const handleInstantDemo = async () => {
    setSubmitting(true);
    setError(null);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ demo: true }),
      });

      const data = await response.json().catch(() => ({}));

      if (data.access_token && typeof window !== "undefined") {
        window.localStorage.setItem("ai_token", data.access_token);
      }

      router.push("/");
      router.refresh();
    } catch {
      setError("Unable to open demo workspace. Please try again.");
      setSubmitting(false);
    }
  };

  const handleSsoSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const workspace = slug.trim().toLowerCase();
    if (!SLUG_REGEX.test(workspace)) {
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
      <span className="login-emblem" aria-hidden="true">
        <Building2 />
      </span>

      <div className="login-heading">
        <p className="eyebrow">WORKSPACE SIGN IN</p>
        <h1>Welcome back</h1>
        <p className="secondary">Sign in to continue to your account intelligence workspace.</p>
      </div>

      {error ? (
        <div className="banner tone-rose" role="alert" style={{ display: "flex", gap: "var(--space-3)", alignItems: "flex-start" }}>
          <AlertCircle aria-hidden="true" style={{ width: 18, height: 18, flexShrink: 0, marginTop: 2 }} />
          <div className="banner-body" style={{ flex: 1 }}>
            <strong>Sign-in failed</strong>
            <p style={{ margin: "2px 0 0" }}>{error}</p>
          </div>
          <button
            type="button"
            className="auth-text-link"
            onClick={() => setError(null)}
            style={{ fontSize: 16, lineHeight: 1, padding: "0 4px" }}
            aria-label="Dismiss error"
          >
            ×
          </button>
        </div>
      ) : null}

      {/* Auth Mode Tabs */}
      <div className="auth-tabs" role="tablist" aria-label="Sign-in methods">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "credentials"}
          className={`auth-tab ${tab === "credentials" ? "active" : ""}`}
          onClick={() => {
            setTab("credentials");
            setError(null);
          }}
        >
          <Mail aria-hidden="true" style={{ width: 14, height: 14 }} />
          <span>Account Login</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "sso"}
          className={`auth-tab ${tab === "sso" ? "active" : ""}`}
          onClick={() => {
            setTab("sso");
            setError(null);
          }}
        >
          <KeyRound aria-hidden="true" style={{ width: 14, height: 14 }} />
          <span>Enterprise SSO</span>
        </button>
      </div>

      {tab === "credentials" ? (
        <>
          {/* Quick Demo Credentials Pill */}
          <div className="demo-credentials-pill" role="note">
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0 }}>
              <Sparkles aria-hidden="true" style={{ width: 15, height: 15, color: "var(--brand)", flexShrink: 0 }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                Demo account: <code>doctor@synthetic.local</code>
              </span>
            </div>
            <button
              type="button"
              className="demo-pill-btn"
              onClick={fillDemo}
              title="Populate test account credentials"
            >
              Fill Demo
            </button>
          </div>

          <form className="login-form" onSubmit={handleCredentialsSubmit} noValidate>
            <div className="field">
              <label htmlFor="client-email">Work Email</label>
              <div className="field-icon-wrap">
                <Mail className="field-leading-icon" aria-hidden="true" />
                <input
                  className="input"
                  id="client-email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  spellCheck={false}
                  placeholder="alex@company.com"
                  value={email}
                  disabled={submitting}
                  required
                  onChange={(e) => {
                    setEmail(e.target.value);
                    if (error) setError(null);
                  }}
                />
              </div>
            </div>

            <div className="field">
              <label htmlFor="client-password">Password</label>
              <div className="field-icon-wrap">
                <Lock className="field-leading-icon" aria-hidden="true" />
                <input
                  className="input has-trailing"
                  id="client-password"
                  name="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  placeholder="••••••••••••"
                  value={password}
                  disabled={submitting}
                  required
                  onChange={(e) => {
                    setPassword(e.target.value);
                    if (error) setError(null);
                  }}
                />
                <button
                  type="button"
                  className="field-trailing-btn"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                </button>
              </div>
            </div>

            <div className="auth-options-row">
              <label className="auth-checkbox-label">
                <input
                  type="checkbox"
                  checked={remember}
                  disabled={submitting}
                  onChange={(e) => setRemember(e.target.checked)}
                />
                <span>Remember me on this device</span>
              </label>
            </div>

            <button className="btn primary lg auth-submit" type="submit" disabled={submitting}>
              {submitting ? (
                <>
                  <span className="spinner" aria-hidden="true" />
                  <span>Signing in…</span>
                </>
              ) : (
                <>
                  <span>Sign in to Workspace</span>
                  <ArrowRight aria-hidden="true" />
                </>
              )}
            </button>
          </form>

          <div className="auth-divider">
            <span>or explore with demo</span>
          </div>

          <button
            type="button"
            className="btn ghost sm"
            onClick={handleInstantDemo}
            disabled={submitting}
            style={{ width: "100%", border: "1px solid var(--border-strong)", borderRadius: "var(--radius-sm)" }}
          >
            <Sparkles aria-hidden="true" style={{ width: 14, height: 14, color: "var(--brand)" }} />
            <span>Launch Demo Workspace Instantly</span>
          </button>
        </>
      ) : (
        <form className="login-form" onSubmit={handleSsoSubmit} noValidate>
          <div className="field">
            <label htmlFor="workspace">Workspace Identifier</label>
            <div className="field-icon-wrap">
              <Building2 className="field-leading-icon" aria-hidden="true" />
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
                onChange={(event) => {
                  setSlug(event.target.value);
                  if (error) setError(null);
                }}
              />
            </div>
            <span className="hint" id="workspace-hint">
              Enter the unique workspace slug assigned to your organization (e.g. acme-corp).
            </span>
          </div>

          <button className="btn primary lg auth-submit" type="submit">
            <span>Continue to SSO</span>
            <ArrowRight aria-hidden="true" />
          </button>
        </form>
      )}

      <p className="login-security-note">
        <LockKeyhole aria-hidden="true" />
        <span>Enterprise zero-trust protection · SOC 2 Type II certified</span>
      </p>

      <div style={{ textAlign: "center", paddingTop: "var(--space-2)" }}>
        <Link href="/operator/login" className="auth-panel-link">
          Operator or staff member? Go to Staff Console →
        </Link>
      </div>
    </section>
  );
}
