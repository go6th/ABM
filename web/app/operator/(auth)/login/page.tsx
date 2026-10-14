// The admin sign-in, in the shape of the workspace sign-in (PDF page 1) with the admin console's
// navy: "Sign in with Google" starts the operator Google sign-in (/api/operator/login), whose
// callback keeps every check (verified email, the 6th domain, the operator or support list, a
// fresh two-step sign-in). A server component: it reads the error code from the query and whether
// staff sign-in is set up on this server.
import Link from "next/link";
import type { Metadata } from "next";
import { AuthFrame, AuthPitch, GoogleMark, type PitchPoint } from "@/ui/AuthFrame";
import { googleOperatorOidcConfig } from "@/lib/operator-oidc";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Admin sign in · 6th Admin" };

const ERRORS: Record<string, string> = {
  denied: "Google sign-in was cancelled. Try again when you are ready.",
  invalid_callback: "That sign-in could not be checked. Start again.",
  exchange_failed: "Google could not finish the sign-in. Try again.",
  invalid_token_response: "That sign-in could not be checked. Start again.",
  invalid_nonce: "That sign-in could not be checked. Start again.",
  staff_access_refused: "This Google account is not on the 6th staff list. If it should be, sign in to Google again with two-step verification and retry.",
};

const POINTS: PitchPoint[] = [
  {
    icon: "chart",
    title: "Counts, never content",
    body: "Admins see usage counts and question categories. Question text and client files never leave the client workspace.",
  },
  {
    icon: "list",
    title: "Every change is recorded",
    body: "Each change you make is logged here and in the client's own audit log.",
  },
  {
    icon: "clock",
    title: "Checked every night",
    body: "The isolation check confirms no client can read another client's data.",
  },
];

export default async function OperatorLoginPage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  const message = error ? ERRORS[error] ?? "That sign-in could not be completed. Start again." : undefined;
  const configured = googleOperatorOidcConfig() !== null;

  return (
    <AuthFrame
      plane="operator"
      pitch={<AuthPitch title="The 6th admin console." lead="Manage clients, users, the 6th Library and platform defaults." points={POINTS} />}
      below={<>Not 6th staff? <Link href="/login">Go to the workspace sign in</Link></>}
    >
      <div>
        <h1 id="auth-title">Admin sign in</h1>
        <p className="sub">For 6th staff only. Sign in with your 6th Google account or an approved staff Google account.</p>
      </div>
      {message ? <div className="notice error" role="alert"><p>{message}</p></div> : null}
      {configured ? (
        <a className="btn primary block" href="/api/operator/login?return_to=%2Foperator">
          <span className="g-mark"><GoogleMark /></span>
          Sign in with Google
        </a>
      ) : (
        <div className="notice info" role="status"><p>Staff sign-in is not set up on this server.</p></div>
      )}
    </AuthFrame>
  );
}
