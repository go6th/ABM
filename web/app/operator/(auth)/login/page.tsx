"use client";

import Link from "next/link";
import {
  ArrowRight,
  BarChart2,
  Building2,
  ShieldCheck,
  Sparkles,
  Users,
  Zap,
} from "lucide-react";

const ERRORS: Record<string, string> = {
  denied: "Sign-in was cancelled by your identity provider.",
  invalid_callback: "The sign-in response could not be verified. Start again.",
  exchange_failed: "Your identity provider could not complete sign-in. Try again.",
  invalid_token_response: "The sign-in response was invalid. Start again.",
  invalid_nonce: "The sign-in response could not be verified. Start again.",
  staff_access_refused: "This account is not authorized for the operator console or needs a fresh MFA sign-in.",
};

export default async function OperatorLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  const message = error ? ERRORS[error] : undefined;

  return (
    <main className="operator-login-page">
      <div className="operator-login-layout">
        <aside className="operator-login-intro" aria-label="About Operator Console">
          <header className="operator-login-brand">
            <div className="operator-login-logo" aria-hidden="true">
              <span className="operator-login-logo-number">6</span>
              <Sparkles className="operator-login-logo-sparkle" />
            </div>
            <span className="operator-login-wordmark"><strong>6th</strong><span>INTERNAL SYSTEMS</span></span>
            <p className="operator-login-brand-caption">OPERATOR CONSOLE</p>
          </header>

          <div className="operator-login-copy">
            <p className="operator-login-kicker">INTERNAL OPERATIONS</p>
            <h2>Operate with precision.<br />Decide with confidence.</h2>
            <p>
              Manage tenants, monitor system health, review runs, and control costs
              from a single audited workspace built for platform operators.
            </p>
          </div>

          <ul className="operator-login-highlights">
            <li>
              <span className="operator-highlight-icon lavender"><BarChart2 aria-hidden="true" /></span>
              <span><strong>Tenant oversight</strong><small>View health, usage, and spend across all workspaces</small></span>
            </li>
            <li>
              <span className="operator-highlight-icon mint"><ShieldCheck aria-hidden="true" /></span>
              <span><strong>Full audit trail</strong><small>Every action logged, every decision traceable</small></span>
            </li>
            <li>
              <span className="operator-highlight-icon peach"><Zap aria-hidden="true" /></span>
              <span><strong>Run control</strong><small>Start, pause, and investigate research runs in real time</small></span>
            </li>
            <li>
              <span className="operator-highlight-icon sky"><Users aria-hidden="true" /></span>
              <span><strong>Support tools</strong><small>Grant access, review failures, manage announcements</small></span>
            </li>
          </ul>
          <p className="operator-login-foot">Staff access only · All activity is audited and monitored</p>
        </aside>

        <section className="operator-login-card" aria-labelledby="operator-login-title">
          <div className="operator-login-header">
            <p className="operator-login-badge">Operator sign in</p>
            <div className="operator-login-heading">
              <h1 id="operator-login-title">Welcome back</h1>
              <p>Use your authorized Google Workspace account. A recent MFA sign-in is required.</p>
            </div>
          </div>

          {message ? (
            <div className="operator-login-message" role="alert">
              <p>{message}</p>
            </div>
          ) : null}

          <Link
            className="operator-login-submit"
            href="/api/operator/login?return_to=%2Foperator"
          >
            <Building2 className="operator-login-provider-icon" aria-hidden="true" />
            Continue with Google Workspace
            <ArrowRight aria-hidden="true" />
          </Link>

          <div className="operator-login-footer">
            <Link href="/login" className="operator-login-switch">
              Customer sign in
              <ArrowRight aria-hidden="true" />
            </Link>
          </div>
        </section>
      </div>
    </main>
  );
}