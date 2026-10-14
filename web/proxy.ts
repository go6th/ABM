// Per-request Content-Security-Policy with a nonce (VW4). Follows the Next.js 16 guide bundled
// in node_modules/next/dist/docs/01-app/02-guides/content-security-policy.md (read 2026-10-07):
// the nonce is passed to the renderer in the x-nonce request header and Next.js applies it to
// its own scripts. No inline style attributes are used anywhere (style-src has no unsafe-inline).
import { NextRequest, NextResponse } from "next/server";

// Files go from the browser straight to object storage (owner decision 2026-10-14): the storage
// origin (UPLOAD_ORIGIN, e.g. https://storage.googleapis.com, or the dev object store) is the one
// other place the page may connect to. Anything that is not a bare origin is ignored.
const ORIGIN_RE = /^https?:\/\/[a-z0-9.-]+(:\d{1,5})?$/i;
function uploadOrigin(): string {
  const value = (process.env.UPLOAD_ORIGIN ?? "").trim();
  return ORIGIN_RE.test(value) ? ` ${value}` : "";
}

export function proxy(request: NextRequest): NextResponse {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const isDev = process.env.NODE_ENV === "development";
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "img-src 'self' blob:",
    "font-src 'self'",
    `connect-src 'self'${uploadOrigin()}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    "/((?!v1/|_next/static|_next/image|favicon.ico).*)",
  ],
};
