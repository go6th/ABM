"use client";
// The workspace sign-in, as PDF page 1 draws it. Password sign-in with "Keep me signed in on this
// device" (on: the normal long session; off: the cookie ends when the browser closes; it applies
// to both ways of signing in), or
// "Continue with SSO", which looks up the company's sign-in from the email's domain
// (POST /v1/sso/discover) and goes to it. Errors are in plain words and never say whether an
// account or a company exists.

import { FormEvent, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { AuthFrame, AuthPitch, KeyIcon, type PitchPoint } from "@/ui/AuthFrame";
import { ApiError, apiPublic, clearSession } from "@/lib/api";
import type { EmailVerificationSent, SignedIn, SsoDiscovery } from "@/lib/gen/api.gen";

const POINTS: PitchPoint[] = [
  {
    icon: "shield",
    title: "Every fact is graded and sourced",
    body: "Each fact is Confirmed, Probable, Hypothesis or Lead and links to where it came from, so you know what to say on a call and what to check first.",
    grades: true,
  },
  {
    icon: "lock",
    title: "Your work stays in your workspace",
    body: "Each company has its own workspace. Files you add are private to it and never appear in research files you share.",
  },
  {
    icon: "document",
    title: "Ready to use on a call",
    body: "Every account comes with a brief, a senior people list and a coverage tracker, as files you can download. We never send outreach for you.",
  },
];

const TOO_MANY = "Too many attempts, try again in a minute.";
const UNAVAILABLE = "Sign in is unavailable right now. Try again in a moment.";

type Notice = { text: string; tone: "error" | "info"; resend?: boolean };
type TenantChoice = { tenant_id: string; role: string };

/** Where to go after signing in: ?next= when it is a page of this site (never another origin or
 *  back to sign-in), else Ask. */
function returnPath(): string {
  const next = new URLSearchParams(window.location.search).get("next") ?? "";
  if (!next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\") || next.startsWith("/login")) return "/";
  return next;
}

/** The workspaces a 409 "choose a workspace" reply lists; [] for any other error. */
function workspaceChoices(e: unknown): TenantChoice[] {
  if (!(e instanceof ApiError) || e.status !== 409 || e.body?.code !== "tenant_selection_required") return [];
  const tenants = Array.isArray(e.body.tenants) ? e.body.tenants : [];
  return tenants.filter((t): t is TenantChoice =>
    typeof t === "object" && t !== null && typeof (t as TenantChoice).tenant_id === "string" && typeof (t as TenantChoice).role === "string");
}

export default function TenantLoginPage() {
  const router = useRouter();
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true);
  const [tenants, setTenants] = useState<TenantChoice[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [pending, setPending] = useState<"password" | "sso" | "resend" | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice(null);
    setEmailError(null);
    setPending("password");
    try {
      await apiPublic<SignedIn>("POST", "/v1/auth/login", { email, password, remember, ...(tenantId ? { workspace_id: tenantId } : {}) });
      clearSession();
      router.replace(returnPath());
    } catch (e) {
      const choices = workspaceChoices(e);
      const firstTenant = choices[0];
      if (firstTenant) {
        setTenants(choices);
        setTenantId(firstTenant.tenant_id);
        setNotice({ tone: "info", text: "Your email opens more than one workspace. Choose one, then sign in." });
        return;
      }
      const status = e instanceof ApiError ? e.status : 0;
      if (status === 429) return setNotice({ tone: "error", text: TOO_MANY });
      if (status === 401 || status === 422) {
        return setNotice({ tone: "error", text: "That email and password don't match.", resend: status === 401 });
      }
      setNotice({ tone: "error", text: UNAVAILABLE });
    } finally {
      setPending(null);
    }
  }

  async function continueWithSso() {
    setNotice(null);
    const field = emailRef.current;
    if (!email.trim() || (field && !field.validity.valid)) {
      setEmailError(email.trim() ? "Enter your full work email, like name@company.com." : "Enter your work email first, then choose Continue with SSO.");
      field?.focus();
      return;
    }
    setEmailError(null);
    setPending("sso");
    try {
      const found = await apiPublic<SsoDiscovery>("POST", "/v1/sso/discover", { email });
      if (found.next === "sso" && found.start_url?.startsWith("/v1/sso/")) {
        window.location.assign(remember ? found.start_url : `${found.start_url}&remember=false`);
        return;
      }
      setNotice({ tone: "info", text: "Your company signs in with a password here. Enter your password above." });
      passwordRef.current?.focus();
    } catch (e) {
      setNotice({ tone: "error", text: e instanceof ApiError && e.status === 429 ? TOO_MANY : UNAVAILABLE });
    } finally {
      setPending((p) => (p === "sso" ? null : p));
    }
  }

  async function resendVerification() {
    setPending("resend");
    try {
      await apiPublic<EmailVerificationSent>("POST", "/v1/auth/email/resend", { email });
      setNotice({ tone: "info", text: "If this address is waiting to be verified, a new verification link is on its way." });
    } catch (e) {
      setNotice({ tone: "error", text: e instanceof ApiError && e.status === 429 ? TOO_MANY : "A verification link could not be sent right now. Try again later." });
    } finally {
      setPending(null);
    }
  }

  return (
    <AuthFrame
      plane="client"
      pitch={
        <AuthPitch
          title="6th is a go-to-market consultancy."
          lead="Account Intelligence is the research process we run for our own clients, now a workspace your team can use directly."
          points={POINTS}
        />
      }
      below={<>6th team? <Link href="/operator/login">Sign in to the admin console</Link></>}
    >
      <div>
        <h1 id="auth-title">Sign in</h1>
        <p className="sub">Use the work email your team invited. You&apos;ll land in your company&apos;s workspace.</p>
      </div>

      <form onSubmit={signIn}>
        {notice ? (
          <div className={notice.tone === "error" ? "notice error" : "notice info"} role={notice.tone === "error" ? "alert" : "status"}>
            <p>{notice.text}</p>
            {notice.resend ? (
              <button type="button" className="linkish" disabled={pending !== null || !email} onClick={() => void resendVerification()}>
                Waiting to verify your email? Send a new link
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="field">
          <label htmlFor="email">Work email</label>
          <input
            ref={emailRef}
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
            aria-invalid={emailError ? true : undefined}
            aria-describedby={emailError ? "email-error" : undefined}
            onChange={(event) => { setEmail(event.target.value); setEmailError(null); }}
          />
          {emailError ? <p className="field-error" id="email-error">{emailError}</p> : null}
        </div>
        <div className="field">
          <div className="label-row">
            <label htmlFor="password">Password</label>
            <Link href="/forgot">Forgot password?</Link>
          </div>
          <div className="pw">
            <input
              ref={passwordRef}
              id="password"
              className="input"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              required
              maxLength={1024}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <button
              type="button"
              className="reveal"
              aria-label={showPassword ? "Hide password" : "Show password"}
              aria-controls="password"
              onClick={() => setShowPassword((v) => !v)}
            >
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
        </div>
        {tenants.length > 0 ? (
          <div className="field">
            <label htmlFor="tenant">Workspace</label>
            <select id="tenant" className="select" value={tenantId} onChange={(event) => setTenantId(event.target.value)}>
              {tenants.map((tenant) => <option key={tenant.tenant_id} value={tenant.tenant_id}>{tenant.tenant_id} ({tenant.role})</option>)}
            </select>
          </div>
        ) : null}
        <label className="check">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Keep me signed in on this device
        </label>
        <button className="btn primary block" type="submit" disabled={pending !== null}>
          {pending === "password" ? "Signing in" : "Sign in"}
        </button>
      </form>

      <p className="or">or</p>

      <div className="sso-block">
        <button type="button" className="btn block sso" disabled={pending !== null} aria-describedby="sso-note" onClick={() => void continueWithSso()}>
          <KeyIcon />
          {pending === "sso" ? "Finding your sign-in" : "Continue with SSO"}
        </button>
        <p className="sso-note" id="sso-note">We&apos;ll find your company&apos;s sign-in from your work email.</p>
      </div>

      <p className="auth-foot">Don&apos;t have access? Ask your 6th contact to send you an invite.</p>
      <p className="auth-foot"><Link href="/privacy">Privacy</Link> · <Link href="/terms">Terms</Link></p>
    </AuthFrame>
  );
}
