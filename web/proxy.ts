// Per-request Content-Security-Policy with a nonce (VW4). Follows the Next.js 16 guide bundled
// in node_modules/next/dist/docs/01-app/02-guides/content-security-policy.md (read 2026-10-07):
// the nonce is passed to the renderer in the x-nonce request header and Next.js applies it to
// its own scripts. No inline style attributes are used anywhere (style-src has no unsafe-inline).
import { NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest): NextResponse {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const isDev = process.env.NODE_ENV === "development";
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "img-src 'self' blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const unavailableOperatorRoutes = new Set([
    "/operator/announcements",
    "/operator/audit",
    "/operator/monitors",
    "/operator/review",
  ]);
  const normalizedPath = request.nextUrl.pathname.replace(/\/$/, "") || "/";
  const response =
    process.env.NODE_ENV === "production" && unavailableOperatorRoutes.has(normalizedPath)
      ? new NextResponse("Not Found", { status: 404, headers: { "Content-Security-Policy": csp } })
      : NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    "/((?!v1/|_next/static|_next/image|favicon.ico).*)",
  ],
};
