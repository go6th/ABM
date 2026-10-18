// Operator sign-in: Google Workspace through /api/operator/login (verified by the backend), with
// error messages by code. A server component: it reads the error code from the query.
import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import { AuthBrand, AuthFrame } from "@/ui/AuthFrame";

const ERRORS: Record<string, string> = {
  denied: "Sign-in was cancelled by your identity provider.",
  invalid_callback: "The sign-in response could not be verified. Start again.",
  exchange_failed: "Your identity provider could not complete sign-in. Try again.",
  invalid_token_response: "The sign-in response was invalid. Start again.",
  invalid_nonce: "The sign-in response could not be verified. Start again.",
  staff_access_refused: "This account is not authorized for the operator console or needs a fresh multi-factor sign-in.",
};

export default async function OperatorLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  const message = error ? ERRORS[error] : undefined;

  return (
    <AuthFrame plane="operator">
      <AuthBrand plane="operator" />
      <div>
        <h1 id="auth-title">Staff sign in</h1>
        <p className="lede">Use your authorized Google Workspace account. A recent multi-factor sign-in is required.</p>
      </div>
      {message ? <div className="banner error" role="alert"><p className="grow">{message}</p></div> : null}
      <a className="btn primary lg block" href="/api/operator/login?return_to=%2Foperator">Continue with Google Workspace</a>
      <p className="auth-links"><Link className="link" href="/login">Client sign in</Link></p>
      <p className="auth-foot"><ShieldCheck size={16} aria-hidden="true" />Staff access only. Every action is audited.</p>
    </AuthFrame>
  );
}
