import { randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { apiOrigin } from "@/lib/api-origin";
import { bodyLimit, capBody, declaredLength } from "@/lib/proxy-body";
import { isSameOriginRouteRequest } from "@/lib/same-origin";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const FORWARDED_HEADERS = [
  "accept",
  "content-type",
  "idempotency-key",
  "if-none-match",
  "if-range",
  "last-event-id",
  "origin",
  "range",
  "traceparent",
  "x-csrf-token",
] as const;

// __Host-ai_preauth_once is the "Keep me signed in = off" marker the API reads when it finishes an
// SSO sign-in; without it the session would always get the persistent cookie.
const AUTH_COOKIES = new Set(["__Host-ai_session", "__Host-ai_preauth", "__Host-ai_preauth_once"]);
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function tenantCookies(raw: string): string {
  return raw
    .split(";")
    .map((part) => part.trim())
    .filter((part) => AUTH_COOKIES.has(part.split("=", 1)[0] ?? ""))
    .join("; ");
}

function problem(status: number, title: string, detail: string): NextResponse {
  return NextResponse.json(
    { type: `urn:6th:problem:${status}`, title, status, detail },
    { status, headers: { "Content-Type": "application/problem+json", "Cache-Control": "no-store" } },
  );
}

async function handleProxy(request: NextRequest): Promise<NextResponse> {
  if (!SAFE_METHODS.has(request.method)) {
    if (!isSameOriginRouteRequest(request)) {
      return problem(403, "Request refused", "A same-origin request is required.");
    }
  }
  const origin = apiOrigin();
  if (!origin) return problem(503, "Service Unavailable", "The upstream API service is temporarily unavailable.");
  const targetUrl = new URL(request.nextUrl.pathname + request.nextUrl.search, origin);

  const forwardHeaders = new Headers();
  forwardHeaders.set("x-request-id", randomUUID());
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value) forwardHeaders.set(name, value);
  }
  const cookie = tenantCookies(request.headers.get("cookie") ?? "");
  if (cookie) forwardHeaders.set("cookie", cookie);

  // The body is streamed, never buffered, and cut off at the route's size limit.
  const limit = bodyLimit(request.method, request.nextUrl.pathname);
  const declared = declaredLength(request.headers.get("content-length"));
  if (declared !== null && declared > limit) return problem(413, "Request too large", "The request body is too large.");
  const capped = request.method !== "GET" && request.method !== "HEAD" && request.body && declared !== 0
    ? capBody(request.body, limit)
    : null;

  try {
    const upstream = await fetch(targetUrl.toString(), {
      method: request.method,
      headers: forwardHeaders,
      body: capped?.stream,
      ...(capped ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
    });

    const responseHeaders = new Headers();
    upstream.headers.forEach((value, key) => {
      const lower = key.toLowerCase();
      if (!["content-encoding", "content-length", "transfer-encoding", "connection"].includes(lower)) {
        responseHeaders.set(key, value);
      }
    });

    if (typeof upstream.headers.getSetCookie === "function") {
      const cookies = upstream.headers.getSetCookie();
      for (const c of cookies) {
        responseHeaders.append("set-cookie", c);
      }
    }

    return new NextResponse(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch (err) {
    if (capped?.exceeded()) return problem(413, "Request too large", "The request body is too large.");
    // the path only: the query string can carry a one-time token
    console.error(`[API Proxy] Connection to ${request.nextUrl.pathname} failed:`, err instanceof Error ? err.name : "error");
    return problem(503, "Service Unavailable", "The upstream API service is temporarily unavailable.");
  }
}

export const GET = handleProxy;
export const POST = handleProxy;
export const PUT = handleProxy;
export const PATCH = handleProxy;
export const DELETE = handleProxy;
export const HEAD = handleProxy;
export const OPTIONS = handleProxy;
