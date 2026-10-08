import { googleOperatorOidcConfig } from "@/lib/operator-oidc";

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
      <h1>Operator sign in</h1>
      <p className="secondary">Use your 6th staff account. Staff access is separate from every customer workspace.</p>
      {errorMessage ? <div className="banner tone-rose" role="alert"><span aria-hidden="true">!</span><div className="banner-body"><strong>Sign-in failed</strong><p>{errorMessage}</p></div></div> : null}
      {loginUrl ? (
        <a className="btn primary lg operator-login-button" href={loginUrl}>
          Continue with Google Workspace <span aria-hidden="true">↗</span>
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
        <strong>Admin access</strong>
        <p>Rajesh’s verified Workspace email must be listed in <code>STAFF_OPERATOR_EMAILS</code>. The API also checks the signed Workspace domain, ID token audience, expiry, and a recent MFA assertion.</p>
      </div>
      <p className="operator-auth-foot">This console contains internal operational data. Access attempts are verified by the API and audited.</p>
    </section>
  );
}
