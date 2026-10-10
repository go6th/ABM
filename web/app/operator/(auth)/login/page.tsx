import Link from "next/link";

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
    <section className="operator-login-card">
      <p className="eyebrow">6th · Internal systems</p>
      <h1>Operator sign in</h1>
      <p className="muted">Use your authorized Google Workspace account. A recent MFA sign-in is required.</p>
      {message ? <p className="error" role="alert">{message}</p> : null}
      <Link className="button primary" href="/api/operator/login?return_to=%2Foperator">
        Continue with Google Workspace
      </Link>
      <p className="muted small">Operator access is separate from customer accounts and is audited.</p>
      <Link href="/login">Customer sign in</Link>
    </section>
  );
}
