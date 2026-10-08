export interface GoogleOperatorOidcConfig {
  clientId: string;
  clientSecret: string;
  redirectUri: URL;
  workspaceDomain: string;
  secureCookies: boolean;
}

export function operatorApiOrigin(): URL | null {
  const value = process.env.API_ORIGIN ?? "http://127.0.0.1:8080";
  try {
    const origin = new URL(value);
    const localHttp = process.env.NODE_ENV !== "production" && origin.protocol === "http:" && ["localhost", "127.0.0.1"].includes(origin.hostname);
    if ((!localHttp && origin.protocol !== "https:") ||
        origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) return null;
    return new URL(origin.origin);
  } catch {
    return null;
  }
}

export function googleOperatorOidcConfig(): GoogleOperatorOidcConfig | null {
  const clientId = process.env.OPERATOR_GOOGLE_CLIENT_ID;
  const clientSecret = process.env.OPERATOR_GOOGLE_CLIENT_SECRET;
  const redirectValue = process.env.OPERATOR_GOOGLE_REDIRECT_URI;
  const workspaceDomain = process.env.STAFF_WORKSPACE_DOMAIN?.trim().toLowerCase();
  if (!clientId || !clientSecret || !redirectValue || !workspaceDomain || !operatorApiOrigin()) return null;
  if (!/^[a-z0-9.-]{3,253}$/.test(workspaceDomain) || workspaceDomain.startsWith(".") || workspaceDomain.endsWith(".")) return null;
  try {
    const redirectUri = new URL(redirectValue);
    const localHttp = process.env.NODE_ENV !== "production" && redirectUri.protocol === "http:" && ["localhost", "127.0.0.1"].includes(redirectUri.hostname);
    if ((!localHttp && redirectUri.protocol !== "https:") ||
        redirectUri.username || redirectUri.password || redirectUri.search || redirectUri.hash ||
        redirectUri.pathname !== "/api/operator/callback") return null;
    return {
      clientId,
      clientSecret,
      redirectUri,
      workspaceDomain,
      secureCookies: redirectUri.protocol === "https:",
    };
  } catch {
    return null;
  }
}

export function operatorOAuthCookieNames(secure: boolean) {
  const prefix = secure ? "__Host-6th_operator_" : "6th_operator_";
  return {
    state: prefix + "oauth_state",
    nonce: prefix + "oauth_nonce",
    verifier: prefix + "oauth_verifier",
    returnTo: prefix + "oauth_return",
    session: secure ? "__Host-6th_operator_session" : "6th_operator_session",
  };
}

export function safeOperatorReturnPath(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/operator";
  try {
    const parsed = new URL(value, "https://operator.invalid");
    if (parsed.origin !== "https://operator.invalid") return "/operator";
    return parsed.pathname + parsed.search + parsed.hash;
  } catch {
    return "/operator";
  }
}
