import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function POST(request: NextRequest): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { type: "invalid_json", status: 400, detail: "Invalid request payload." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  const { email, password, demo } = (body as {
    email?: unknown;
    password?: unknown;
    demo?: unknown;
  }) ?? {};

  const emailStr = typeof email === "string" ? email.trim().toLowerCase() : "";
  const passwordStr = typeof password === "string" ? password : "";
  const isDemo = Boolean(demo);

  if (!isDemo) {
    if (!emailStr || !EMAIL_REGEX.test(emailStr)) {
      return NextResponse.json(
        { type: "invalid_email", status: 422, detail: "Please provide a valid work email." },
        { status: 422, headers: { "Cache-Control": "no-store" } },
      );
    }
    if (!passwordStr || passwordStr.length < 6) {
      return NextResponse.json(
        { type: "invalid_password", status: 422, detail: "Password must be at least 6 characters." },
        { status: 422, headers: { "Cache-Control": "no-store" } },
      );
    }
  }

  // Attempt minting a token via fake-idp (available in dev/test/compose environments)
  const idpHosts = [
    process.env.FAKE_IDP_URL,
    "http://fake-idp:9090",
    "http://127.0.0.1:9090",
    "http://localhost:9090",
  ].filter(Boolean) as string[];

  let token: string | null = null;
  const sub = isDemo ? "doctor" : (emailStr.split("@")[0] || "doctor");

  for (const host of idpHosts) {
    try {
      const response = await fetch(`${host.replace(/\/$/, "")}/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sub: "doctor", // Uses established tenant membership in DB
          tenant_id: "00000000-0000-4000-8000-00000000d0c7",
          role: "admin",
          mfa: true,
        }),
        cache: "no-store",
      });
      if (response.ok) {
        const data = await response.json();
        if (typeof data === "object" && data !== null && typeof data.access_token === "string") {
          token = data.access_token;
          break;
        }
      }
    } catch {
      // Try next host
    }
  }

  const response = NextResponse.json(
    {
      authenticated: true,
      access_token: token,
      user_id: sub,
      role: "admin",
      return_to: "/",
    },
    { headers: { "Cache-Control": "no-store" } },
  );

  if (token) {
    const isHttps = request.nextUrl.protocol === "https:";
    response.cookies.set("ai_token", token, {
      httpOnly: false, // Accessible by client JS to set Authorization header
      secure: isHttps,
      sameSite: "lax",
      path: "/",
      maxAge: 7 * 86400,
    });
  }

  return response;
}
