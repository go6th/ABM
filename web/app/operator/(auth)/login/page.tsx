import { googleOperatorOidcConfig } from "@/lib/operator-oidc";
import { ArrowRight, ShieldCheck } from "lucide-react";

function localReturnPath(value: string | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/operator";
  return value;
}

export default async function OperatorLogin({
  searchParams,
}: {
  searchParams: Promise<{ return_to?: string; error?: string }>;
}) {
  const { return_to: returnToParam, error } = await searchParams;
  const returnTo = localReturnPath(returnToParam);
  const loginUrl = googleOperatorOidcConfig()
    ? "/api/operator/login?return_to=" + encodeURIComponent(returnTo)
    : null;
  const errorMessage = error === "denied"
    ? "Google sign-in was cancelled."
    : error === "staff_access_refused"
      ? "This staff account is not allowlisted, is outside the configured Workspace, or did not complete recent MFA."
      : error
        ? "Staff sign-in could not be completed. Check the Workspace and staff identity configuration."
        : null;

  return (
    <section className="operator-login-content">
      <p className="eyebrow">STAFF IDENTITY</p>
      <h1>Welcome back</h1>
      <p className="secondary">Sign in with your approved staff account to access the operations dashboard.</p>
      {errorMessage ? <div className="banner tone-rose" role="alert"><span aria-hidden="true">!</span><div className="banner-body"><strong>Sign-in failed</strong><p>{errorMessage}</p></div></div> : null}
      {loginUrl ? (
        <a className="btn primary lg operator-login-button" href={loginUrl}>
          Continue with Google Workspace <ArrowRight aria-hidden="true" />
        </a>
      ) : (
        <div className="banner tone-butter operator-login-warning" role="status">
          <span aria-hidden="true">◷</span>
          <div className="banner-body">
            <strong>Staff sign in is not configured</strong>
            <p>Set the Google Workspace OAuth client, callback URI and workspace domain in the server environment to enable staff sign-in.</p>
          </div>
        </div>
      )}
      <div className="operator-login-note">
        <ShieldCheck aria-hidden="true" />
        <div><strong>Protected staff access</strong>
        <p>Only approved accounts in your Workspace can continue. The service verifies identity and recent multi-factor authentication.</p></div>
      </div>
      <p className="operator-auth-foot">This console is separate from customer workspaces. Access is verified and audited.</p>
    </section>
  );
}
