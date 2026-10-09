import { randomBytes, createHash } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { googleOperatorOidcConfig, operatorOAuthCookieNames, safeOperatorReturnPath } from "@/lib/operator-oidc";
import { signStaffSession, sanitizeStaffId } from "@/lib/operator-session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function isOriginAllowed(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    const originUrl = new URL(origin);
    const requestUrl = new URL(request.url);
    return originUrl.origin === requestUrl.origin;
  } catch {
    return false;
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

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isOriginAllowed(request)) {
    return NextResponse.json(
      { type: "origin_refused", title: "Forbidden", status: 403, detail: "Request origin could not be verified." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { type: "invalid_json", title: "Bad Request", status: 400, detail: "A valid JSON request is required." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  if (typeof body !== "object" || body === null) {
    return NextResponse.json(
      { type: "invalid_body", title: "Bad Request", status: 400, detail: "Invalid request payload." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  const { email, password, role, remember, return_to } = body as {
    email?: unknown;
    password?: unknown;
    role?: unknown;
    remember?: unknown;
    return_to?: unknown;
  };

  const emailStr = typeof email === "string" ? email.trim().toLowerCase() : "";
  const passwordStr = typeof password === "string" ? password : "";
  const requestedRole = role === "support" ? "support" : "operator";
  const rememberMe = Boolean(remember);
  const returnTo = safeOperatorReturnPath(typeof return_to === "string" ? return_to : undefined);

  if (!emailStr || !EMAIL_REGEX.test(emailStr)) {
    return NextResponse.json(
      { type: "invalid_email", title: "Validation Error", status: 422, detail: "Please provide a valid work email address." },
      { status: 422, headers: { "Cache-Control": "no-store" } },
    );
  }

  if (!passwordStr || passwordStr.length < 6) {
    return NextResponse.json(
      { type: "invalid_password", title: "Validation Error", status: 422, detail: "Password must be at least 6 characters." },
      { status: 422, headers: { "Cache-Control": "no-store" } },
    );
  }

  // Check against explicit environment operator allowlist if set
  const allowedEmails = process.env.STAFF_OPERATOR_EMAILS
    ? process.env.STAFF_OPERATOR_EMAILS.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)
    : [];
  const allowedSupportEmails = process.env.STAFF_SUPPORT_EMAILS
    ? process.env.STAFF_SUPPORT_EMAILS.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)
    : [];

  let effectiveRole: "operator" | "support" = requestedRole;

  if (allowedEmails.length > 0 || allowedSupportEmails.length > 0) {
    if (allowedEmails.includes(emailStr)) {
      effectiveRole = "operator";
    } else if (allowedSupportEmails.includes(emailStr)) {
      effectiveRole = "support";
    } else if (process.env.NODE_ENV === "production") {
      return NextResponse.json(
        { type: "unauthorized_staff", title: "Access Denied", status: 403, detail: "This staff account is not on the approved operator list." },
        { status: 403, headers: { "Cache-Control": "no-store" } },
      );
    }
  }

  // Calculate session lifespan
  const maxAge = rememberMe ? 7 * 86400 : 8 * 3600; // 7 days or 8 hours
  const exp = Math.floor(Date.now() / 1000) + maxAge;
  const staffId = sanitizeStaffId(emailStr);

  const token = signStaffSession({
    staff_id: staffId,
    email: emailStr,
    role: effectiveRole,
    mfa_fresh: true,
    exp,
  });

  const secure = googleOperatorOidcConfig()?.secureCookies ?? (process.env.NODE_ENV === "production");
  const cookieNames = operatorOAuthCookieNames(secure);

  const response = NextResponse.json(
    {
      authenticated: true,
      staff_id: staffId,
      email: emailStr,
      role: effectiveRole,
      return_to: returnTo,
    },
    { headers: { "Cache-Control": "no-store" } },
  );

  response.cookies.set(cookieNames.session, token, {
    httpOnly: true,
    secure,
    sameSite: "lax",
    path: "/",
    maxAge,
  });

  return response;
}
