import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { isSameOriginRouteRequest } from "@/lib/same-origin";
import { OperatorSession } from "@/lib/gen/api.gen";
import { googleOperatorOidcConfig, operatorApiOrigin, operatorOAuthCookieNames } from "@/lib/operator-oidc";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const sessionCookieName = () => operatorOAuthCookieNames(
  googleOperatorOidcConfig()?.secureCookies ?? process.env.NODE_ENV === "production",
).session;

function problem(status: number, type: string, detail: string): NextResponse {
  return NextResponse.json(
    { type: `urn:6th:problem:${type}`, title: "Operator sign-in refused", status, detail },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

async function verifyToken(token: string): Promise<{ staff_id: string; role: string; mfa_fresh: boolean } | null> {
  try {
    const apiOrigin = operatorApiOrigin();
    if (!apiOrigin) return null;
    const reply = await fetch(new URL("/operator/v1/session", apiOrigin), {
      method: "GET",
      headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
      cache: "no-store",
      redirect: "manual",
    });
    if (!reply.ok) return null;
    return OperatorSession.parse(await reply.json());
  } catch {
    return null;
  }
}

export async function GET(): Promise<NextResponse> {
  const cookie = sessionCookieName();
  const token = (await cookies()).get(cookie)?.value;
  if (!token) return problem(401, "operator_session_required", "Staff sign-in is required.");

  const session = await verifyToken(token);
  if (!session || !session.mfa_fresh) {
    const response = problem(401, "operator_session_invalid", "Staff sign-in must be renewed.");
    response.cookies.delete(cookie);
    return response;
  }
  return NextResponse.json(session, { headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOriginRouteRequest(request)) return problem(403, "origin_refused", "The request origin could not be verified.");
  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(contentLength) && contentLength > 16_384) {
    return problem(413, "body_too_large", "Sign-in response is too large.");
  }
  if (!(request.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) {
    return problem(415, "content_type_refused", "A JSON sign-in response is required.");
  }

  let value: unknown;
  try {
    value = await request.json();
  } catch {
    return problem(400, "invalid_sign_in_response", "The sign-in response is invalid.");
  }
  if (typeof value !== "object" || value === null || !("access_token" in value)) {
    return problem(400, "invalid_sign_in_response", "The sign-in response is invalid.");
  }
  const token = (value as { access_token?: unknown }).access_token;
  if (typeof token !== "string" || token.length < 16 || token.length > 12_000) {
    return problem(400, "invalid_sign_in_response", "The sign-in response is invalid.");
  }

  const sessionResult = await verifyToken(token);

  if (!sessionResult) return problem(401, "invalid_staff_token", "Staff sign-in could not be verified.");
  if (!sessionResult.mfa_fresh) return problem(403, "step_up_required", "A recent staff MFA sign-in is required.");

  const response = NextResponse.json(
    { authenticated: true, staff_id: sessionResult.staff_id, role: sessionResult.role },
    { headers: { "Cache-Control": "no-store" } },
  );
  response.cookies.set(sessionCookieName(), token, {
    httpOnly: true,
    secure: googleOperatorOidcConfig()?.secureCookies ?? process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
  });
  return response;
}

export async function DELETE(request: NextRequest): Promise<NextResponse> {
  if (!isSameOriginRouteRequest(request)) return problem(403, "origin_refused", "The request origin could not be verified.");
  const response = NextResponse.json({ signed_out: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(sessionCookieName(), "", {
    httpOnly: true,
    secure: googleOperatorOidcConfig()?.secureCookies ?? process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: 0,
  });
  return response;
}
