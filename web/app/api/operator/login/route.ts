import { createHash, randomBytes } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { googleOperatorOidcConfig, operatorOAuthCookieNames, safeOperatorReturnPath } from "@/lib/operator-oidc";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const config = googleOperatorOidcConfig();
  if (!config) {
    return NextResponse.json(
      { type: "operator_sso_not_configured", status: 503, detail: "Staff sign-in is not configured." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  if (request.nextUrl.origin !== config.redirectUri.origin) {
    const canonicalLogin = new URL("/api/operator/login", config.redirectUri);
    canonicalLogin.searchParams.set("return_to", safeOperatorReturnPath(request.nextUrl.searchParams.get("return_to")));
    return NextResponse.redirect(canonicalLogin, 303);
  }

  const state = randomBytes(32).toString("base64url");
  const nonce = randomBytes(32).toString("base64url");
  const verifier = randomBytes(48).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const authorize = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  authorize.searchParams.set("client_id", config.clientId);
  authorize.searchParams.set("redirect_uri", config.redirectUri.toString());
  authorize.searchParams.set("response_type", "code");
  authorize.searchParams.set("scope", "openid email");
  authorize.searchParams.set("state", state);
  authorize.searchParams.set("nonce", nonce);
  authorize.searchParams.set("code_challenge", challenge);
  authorize.searchParams.set("code_challenge_method", "S256");
  authorize.searchParams.set("prompt", "select_account");
  authorize.searchParams.set("max_age", "600");
  authorize.searchParams.set("hd", config.workspaceDomain);
  authorize.searchParams.set("claims", JSON.stringify({
    id_token: {
      amr: { essential: true },
      auth_time: { essential: true },
    },
  }));

  const names = operatorOAuthCookieNames(config.secureCookies);
  const response = NextResponse.redirect(authorize, 302);
  const cookieOptions = {
    httpOnly: true,
    secure: config.secureCookies,
    sameSite: "lax" as const,
    path: "/",
    maxAge: 600,
  };
  response.cookies.set(names.state, state, cookieOptions);
  response.cookies.set(names.nonce, nonce, cookieOptions);
  response.cookies.set(names.verifier, verifier, cookieOptions);
  response.cookies.set(names.returnTo, safeOperatorReturnPath(request.nextUrl.searchParams.get("return_to")), cookieOptions);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
