// The web server's Google ID token for the API (fix prompt D, task 6; ADR-0127). The API's Cloud
// Run service no longer lets allUsers invoke it: only the web service account holds
// roles/run.invoker, so every server-side call to the API carries an ID token for the API's
// audience. It goes in X-Serverless-Authorization, which Cloud Run checks for IAM while leaving
// Authorization to the application (the operator proxy sends the staff bearer token there).
//
// On Cloud Run (K_SERVICE is set) the token comes from the metadata server, cached until five
// minutes before it expires; google-auth-library is not a dependency and is not needed for this.
// Anywhere else (dev, tests, the local stack) no token is fetched and no header is added.
const METADATA_IDENTITY =
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";
const REFRESH_BEFORE_MS = 5 * 60_000;
const METADATA_TIMEOUT_MS = 2_000;

export const SERVERLESS_AUTH_HEADER = "x-serverless-authorization";

const cache = new Map<string, { token: string; expiresAt: number }>();

/** Forget cached tokens (tests). */
export function clearIdTokenCache(): void {
  cache.clear();
}

function onCloudRun(): boolean {
  return Boolean(process.env.K_SERVICE?.trim());
}

function expiryOf(token: string, now: number): number {
  try {
    const payload = token.split(".")[1] ?? "";
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as { exp?: unknown };
    if (typeof claims.exp === "number") return claims.exp * 1000;
  } catch {
    // an unreadable payload: assume the metadata server's usual one hour
  }
  return now + 60 * 60_000;
}

/** An ID token for `audience` (the API origin), or null off Cloud Run. Throws when the metadata
 * server does not answer on Cloud Run: the caller fails the request as an upstream failure. */
export async function idTokenFor(audience: string): Promise<string | null> {
  if (!onCloudRun()) return null;
  const now = Date.now();
  const hit = cache.get(audience);
  if (hit && hit.expiresAt - now > REFRESH_BEFORE_MS) return hit.token;
  const url = `${METADATA_IDENTITY}?audience=${encodeURIComponent(audience)}`;
  const reply = await fetch(url, {
    headers: { "Metadata-Flavor": "Google" },
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(METADATA_TIMEOUT_MS),
  });
  if (!reply.ok) throw new Error(`metadata identity ${reply.status}`);
  const token = (await reply.text()).trim();
  if (!token) throw new Error("metadata identity empty");
  cache.set(audience, { token, expiresAt: expiryOf(token, now) });
  return token;
}

/** The header to add to a server-side call to the API at `origin`: {} off Cloud Run. */
export async function serverlessAuth(origin: string | URL): Promise<Record<string, string>> {
  const token = await idTokenFor(new URL(origin).origin);
  return token ? { [SERVERLESS_AUTH_HEADER]: `Bearer ${token}` } : {};
}
