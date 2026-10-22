// The cookies the web tier forwards to the API, and nothing else: the session, the SSO
// pre-authentication marker and the "Keep me signed in = off" marker the API reads when it
// finishes an SSO sign-in (without it the session would always get the persistent cookie).
// Shared by the browser proxy, Server Components and the logout route so one list governs all.
export const AUTH_COOKIES = new Set(["__Host-ai_session", "__Host-ai_preauth", "__Host-ai_preauth_once"]);

/** The tenant cookies from a Cookie header, in their original order; "" when none is present. */
export function tenantCookies(raw: string): string {
  return raw
    .split(";")
    .map((part) => part.trim())
    .filter((part) => AUTH_COOKIES.has(part.split("=", 1)[0] ?? ""))
    .join("; ");
}
