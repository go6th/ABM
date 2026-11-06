// The operator console's session (fix prompt B, task 3; ADR-0111). The browser's cookie never holds
// the staff IdP's token: right after the token is verified, the web tier trades it ONCE, server
// side, for an opaque console session the API mints (POST /operator/v1/sessions: 256 random bits,
// only its SHA-256 stored, 30 minutes idle and 8 hours absolute). The cookie holds that session
// token, which the operator proxy presents as the bearer; sign-out revokes it upstream.
import { operatorApiOrigin } from "@/lib/operator-oidc";
import { serverlessAuth } from "@/lib/serverless-auth";

export interface MintedSession { token: string; expiresAt: Date }

const SESSION_TOKEN = /^ops_[A-Za-z0-9_-]{43,}$/;

/** Trade a verified staff IdP token for a console session; null when the API refuses. */
export async function mintOperatorSession(idpToken: string): Promise<MintedSession | null> {
  const origin = operatorApiOrigin();
  if (!origin) return null;
  try {
    const reply = await fetch(new URL("/operator/v1/sessions", origin), {
      method: "POST",
      headers: { ...(await serverlessAuth(origin)), Accept: "application/json", Authorization: "Bearer " + idpToken },
      cache: "no-store",
      redirect: "manual",
    });
    if (reply.status !== 201) return null;
    const body: unknown = await reply.json();
    if (typeof body !== "object" || body === null) return null;
    const { token, expires_at: expires } = body as { token?: unknown; expires_at?: unknown };
    if (typeof token !== "string" || !SESSION_TOKEN.test(token) || typeof expires !== "string") return null;
    const expiresAt = new Date(expires);
    return Number.isNaN(expiresAt.getTime()) ? null : { token, expiresAt };
  } catch {
    return null;
  }
}

/** End a console session upstream (best effort: the cookie is cleared either way). */
export async function revokeOperatorSession(sessionToken: string): Promise<void> {
  const origin = operatorApiOrigin();
  if (!origin || !sessionToken.startsWith("ops_")) return;
  try {
    await fetch(new URL("/operator/v1/sessions/current", origin), {
      method: "DELETE",
      headers: { ...(await serverlessAuth(origin)), Authorization: "Bearer " + sessionToken },
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    // the session still ends at its idle or absolute limit
  }
}
