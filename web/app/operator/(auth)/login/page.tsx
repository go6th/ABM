"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowRight,
  Eye,
  EyeOff,
  Lock,
  Mail,
  ShieldCheck,
  Sparkles,
  AlertCircle,
  HelpCircle,
  CheckCircle2,
} from "lucide-react";

export default function OperatorLogin() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const returnTo = searchParams.get("return_to") ?? "/operator";
  const urlError = searchParams.get("error");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"operator" | "support">("operator");
  const [remember, setRemember] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [error, setError] = useState<string | null>(
    urlError === "denied"
      ? "Google sign-in was cancelled."
      : urlError === "staff_access_refused"
        ? "This staff account is not allowlisted or did not complete required MFA."
        : urlError
          ? "Staff sign-in could not be completed. Check configuration."
          : null,
  );

  const fillDemoCredentials = () => {
    setEmail("operator@example.com");
    setPassword("Operator2026!");
    setRole("operator");
    setError(null);
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanEmail = email.trim();
    if (!cleanEmail) {
      setError("Please enter your staff work email.");
      return;
    }
    if (!password) {
      setError("Please enter your staff password.");
      return;
    }
    if (password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const response = await fetch("/api/operator/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: cleanEmail,
          password,
          role,
          remember,
          return_to: returnTo,
        }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        setError(data.detail ?? data.message ?? "Staff credentials could not be verified.");
        setSubmitting(false);
        return;
      }

      // Successful login - navigate to operator console
      const destination = data.return_to || returnTo || "/operator";
      router.push(destination);
      router.refresh();
    } catch {
      setError("Network connection error. Unable to reach operator authentication service.");
      setSubmitting(false);
    }
  };

  return (
    <section className="operator-login-content">
      <div className="operator-auth-badge">
        <ShieldCheck aria-hidden="true" />
        <span>Staff Identity Portal</span>
      </div>

      <div className="login-heading" style={{ textAlign: "left" }}>
        <h1 style={{ fontSize: "var(--text-2xl)", marginBottom: "var(--space-1)" }}>Staff Sign In</h1>
        <p className="secondary" style={{ fontSize: "var(--text-sm)", margin: 0 }}>
          Enter your staff credentials to access operations, tenant management, and platform controls.
        </p>
      </div>

      {error ? (
        <div className="banner tone-rose" role="alert" style={{ display: "flex", gap: "var(--space-3)", alignItems: "flex-start" }}>
          <AlertCircle aria-hidden="true" style={{ width: 18, height: 18, flexShrink: 0, marginTop: 2 }} />
          <div className="banner-body" style={{ flex: 1 }}>
            <strong>Authentication failed</strong>
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

      {/* Quick Test Demo Fill Pill */}
      <div className="demo-credentials-pill" role="note">
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0 }}>
          <Sparkles aria-hidden="true" style={{ width: 15, height: 15, color: "var(--operator)", flexShrink: 0 }} />
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            Test login: <code>operator@example.com</code> / <code>Operator2026!</code>
          </span>
        </div>
        <button
          type="button"
          className="demo-pill-btn"
          onClick={fillDemoCredentials}
          title="Click to populate demo operator credentials"
        >
          Fill Demo
        </button>
      </div>

      <form className="login-form" onSubmit={handleSubmit} noValidate>
        {/* Role Selector */}
        <div className="field">
          <label style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)", fontWeight: 600 }}>
            Console Access Level
          </label>
          <div className="role-chips" role="radiogroup" aria-label="Staff role selection">
            <button
              type="button"
              role="radio"
              aria-checked={role === "operator"}
              className={`role-chip ${role === "operator" ? "selected" : ""}`}
              onClick={() => setRole("operator")}
            >
              <strong>Operator</strong>
              <small>Full tenant & platform control</small>
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={role === "support"}
              className={`role-chip ${role === "support" ? "selected" : ""}`}
              onClick={() => setRole("support")}
            >
              <strong>Support</strong>
              <small>Read-only & audited triage</small>
            </button>
          </div>
        </div>

        {/* Email Field */}
        <div className="field">
          <label htmlFor="staff-email">Work Email</label>
          <div className="field-icon-wrap">
            <Mail className="field-leading-icon" aria-hidden="true" />
            <input
              className="input"
              id="staff-email"
              name="email"
              type="email"
              autoComplete="email"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="operator@company.com"
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

        {/* Password Field */}
        <div className="field">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <label htmlFor="staff-password">Password</label>
            <button
              type="button"
              className="auth-text-link"
              onClick={() => setShowHelp(!showHelp)}
              style={{ display: "inline-flex", alignItems: "center", gap: 3 }}
            >
              <HelpCircle aria-hidden="true" style={{ width: 12, height: 12 }} />
              Forgot password?
            </button>
          </div>

          <div className="field-icon-wrap">
            <Lock className="field-leading-icon" aria-hidden="true" />
            <input
              className="input has-trailing"
              id="staff-password"
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

        {/* Forgot Password Helper Box */}
        {showHelp ? (
          <div className="banner tone-sky" role="status" style={{ fontSize: "var(--text-xs)" }}>
            <div className="banner-body">
              <strong>Need credentials reset?</strong>
              <p style={{ margin: "2px 0 0" }}>
                Staff credentials are administered centrally by your Security Operations team.
                Contact <code style={{ fontSize: "11px" }}>security@6th.io</code> or your internal administrator.
              </p>
            </div>
          </div>
        ) : null}

        {/* Remember Me Option */}
        <div className="auth-options-row">
          <label className="auth-checkbox-label">
            <input
              type="checkbox"
              checked={remember}
              disabled={submitting}
              onChange={(e) => setRemember(e.target.checked)}
            />
            <span>Keep session active on this device</span>
          </label>
        </div>

        {/* Submit Button */}
        <button
          className="btn primary lg auth-submit"
          type="submit"
          disabled={submitting}
          style={{ marginTop: "var(--space-2)" }}
        >
          {submitting ? (
            <>
              <span className="spinner" aria-hidden="true" />
              <span>Verifying credentials…</span>
            </>
          ) : (
            <>
              <span>Sign in to Operations</span>
              <ArrowRight aria-hidden="true" />
            </>
          )}
        </button>
      </form>

      {/* Security Compliance Note */}
      <div className="operator-login-note" style={{ marginTop: "var(--space-2)" }}>
        <CheckCircle2 aria-hidden="true" />
        <div>
          <strong>Protected internal access</strong>
          <p>Multi-factor verification enforced. All administrative actions and sessions are cryptographically logged for audit compliance.</p>
        </div>
      </div>

      <div style={{ textAlign: "center", paddingTop: "var(--space-2)" }}>
        <Link href="/login" className="auth-panel-link">
          Customer workspace sign in →
        </Link>
      </div>
    </section>
  );
}
