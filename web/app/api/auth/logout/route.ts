import { NextRequest, NextResponse } from "next/server";
import { apiOrigin } from "@/lib/api-origin";
import { isSameOriginRouteRequest } from "@/lib/same-origin";
import { tenantCookies } from "@/lib/tenant-cookies";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOriginRouteRequest(request)) {
    return NextResponse.json(
      { type: "urn:6th:problem:403", title: "Request refused", status: 403, detail: "A same-origin request is required." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  const origin = apiOrigin();
  const cookie = tenantCookies(request.headers.get("cookie") ?? "");
  try {
    if (!origin) throw new Error("no API configured");
    const session = await fetch(new URL("/v1/sso/session", origin), {
      headers: { cookie, accept: "application/json" },
      cache: "no-store",
      redirect: "error",
    });
    if (session.ok) {
      const body = (await session.json()) as { csrf_token?: unknown };
      if (typeof body.csrf_token === "string") {
        await fetch(new URL("/v1/sso/logout", origin), {
          method: "POST",
          headers: { cookie, origin: request.headers.get("origin") ?? "", "x-csrf-token": body.csrf_token, accept: "application/json" },
          cache: "no-store",
          redirect: "error",
        });
      }
    }
  } catch {
    // Expire the browser cookie if the API is unavailable; server expiry remains bounded.
  }

  const response = NextResponse.json({ signed_out: true }, { headers: { "Cache-Control": "no-store" } });
  for (const name of ["__Host-ai_session", "__Host-ai_preauth"]) {
    response.cookies.set(name, "", {
      httpOnly: true,
      secure: true,
      sameSite: "lax",
      path: "/",
      maxAge: 0,
    });
  }
  return response;
}
