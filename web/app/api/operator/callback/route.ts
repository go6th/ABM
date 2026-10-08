import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { OperatorSession } from "@/lib/gen/api.gen";
import { googleOperatorOidcConfig, operatorApiOrigin, operatorOAuthCookieNames, safeOperatorReturnPath } from "@/lib/operator-oidc";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function safeEqual(left: string | undefined, right: string | null): boolean {
  if (!left || !right) return false;
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

function errorRedirect(base: URL, code: string): NextResponse {
  const destination = new URL("/operator/login", base);
  destination.searchParams.set("error", code);
  const response = NextResponse.redirect(destination, 303);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}

function clearOAuthCookies(response: NextResponse, names: ReturnType<typeof operatorOAuthCookieNames>, secure: boolean) {
  for (const name of [names.state, names.nonce, names.verifier, names.returnTo]) {
    response.cookies.set(name, "", { httpOnly: true, secure, sameSite: "lax", path: "/", maxAge: 0 });
  }
}

function tokenNonce(token: string): string | null {
  try {
    const payload = token.split(".")[1];
    if (!payload) return null;
    const claims: unknown = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    return typeof claims === "object" && claims !== null && "nonce" in claims && typeof claims.nonce === "string"
      ? claims.nonce
      : null;
  } catch {
    return null;
  }
}

function tokenExpiry(token: string): Date | undefined {
  try {
    const payload = token.split(".")[1];
    if (!payload) return undefined;
    const claims: unknown = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    if (typeof claims !== "object" || claims === null || !("exp" in claims) || typeof claims.exp !== "number") return undefined;
    return new Date(claims.exp * 1000);
  } catch {
    return undefined;
  }
}

async function verifyStaffToken(token: string): Promise<{ staff_id: string; role: string; mfa_fresh: boolean } | null> {
  try {
    const apiOrigin = operatorApiOrigin();
    if (!apiOrigin) return null;
    const result = await fetch(new URL("/operator/v1/session", apiOrigin), {
      method: "GET",
      headers: { Accept: "application/json", Authorization: "Bearer " + token },
      cache: "no-store",
      redirect: "manual",
    });
    if (!result.ok) return null;
    return OperatorSession.parse(await result.json());
  } catch {
    return null;
  }
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const config = googleOperatorOidcConfig();
  if (!config) {
    return NextResponse.json(
      { type: "operator_sso_not_configured", status: 503, detail: "Staff sign-in is not configured." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  const names = operatorOAuthCookieNames(config.secureCookies);
  const stateCookie = request.cookies.get(names.state)?.value;
  const nonceCookie = request.cookies.get(names.nonce)?.value;
  const verifier = request.cookies.get(names.verifier)?.value;
  const returnTo = safeOperatorReturnPath(request.cookies.get(names.returnTo)?.value);
  const state = request.nextUrl.searchParams.get("state");
  const responseIssuer = request.nextUrl.searchParams.get("iss");
  const code = request.nextUrl.searchParams.get("code");
  const googleError = request.nextUrl.searchParams.get("error");

  if (!safeEqual(stateCookie, state) || responseIssuer !== "https://accounts.google.com" || !nonceCookie || !verifier || googleError || !code) {
    const response = errorRedirect(config.redirectUri, googleError ? "denied" : "invalid_callback");
    clearOAuthCookies(response, names, config.secureCookies);
    return response;
  }

  let tokenPayload: unknown;
  try {
    const body = new URLSearchParams({
      grant_type: "authorization_code",
      code,
      client_id: config.clientId,
      client_secret: config.clientSecret,
      redirect_uri: config.redirectUri.toString(),
      code_verifier: verifier,
    });
    const tokenResponse = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body,
      cache: "no-store",
      redirect: "error",
    });
    if (!tokenResponse.ok) {
      const response = errorRedirect(config.redirectUri, "exchange_failed");
      clearOAuthCookies(response, names, config.secureCookies);
      return response;
    }
    tokenPayload = await tokenResponse.json();
  } catch {
    const response = errorRedirect(config.redirectUri, "exchange_failed");
    clearOAuthCookies(response, names, config.secureCookies);
    return response;
  }

  if (typeof tokenPayload !== "object" || tokenPayload === null || !("id_token" in tokenPayload) || typeof tokenPayload.id_token !== "string") {
    const response = errorRedirect(config.redirectUri, "invalid_token_response");
    clearOAuthCookies(response, names, config.secureCookies);
    return response;
  }
  const idToken = tokenPayload.id_token;
  if (!safeEqual(nonceCookie, tokenNonce(idToken))) {
    const response = errorRedirect(config.redirectUri, "invalid_nonce");
    clearOAuthCookies(response, names, config.secureCookies);
    return response;
  }

  const session = await verifyStaffToken(idToken);
  if (!session || !session.mfa_fresh) {
    const response = errorRedirect(config.redirectUri, "staff_access_refused");
    clearOAuthCookies(response, names, config.secureCookies);
    return response;
  }

  const destination = new URL(returnTo, config.redirectUri);
  const response = NextResponse.redirect(destination, 303);
  const expires = tokenExpiry(idToken);
  response.cookies.set(names.session, idToken, {
    httpOnly: true,
    secure: config.secureCookies,
    sameSite: "strict",
    path: "/",
    ...(expires ? { expires } : {}),
  });
  clearOAuthCookies(response, names, config.secureCookies);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
