import { randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const FORWARDED_HEADERS = [
  "accept",
  "content-type",
  "idempotency-key",
  "if-none-match",
  "last-event-id",
  "origin",
  "traceparent",
  "x-csrf-token",
] as const;

const AUTH_COOKIES = new Set(["__Host-ai_session", "__Host-ai_preauth"]);
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function tenantCookies(raw: string): string {
  return raw
    .split(";")
    .map((part) => part.trim())
    .filter((part) => AUTH_COOKIES.has(part.split("=", 1)[0] ?? ""))
    .join("; ");
}

function getApiOrigin(): string {
  if (process.env.API_ORIGIN) {
    return process.env.API_ORIGIN.replace(/\/$/, "");
  }
  return process.env.NODE_ENV === "production" ? "http://api:8080" : "http://127.0.0.1:8080";
}

async function handleProxy(request: NextRequest): Promise<NextResponse> {
  if (!SAFE_METHODS.has(request.method)) {
    const origin = request.headers.get("origin");
    if (!origin || origin !== request.nextUrl.origin) {
      return NextResponse.json(
        { type: "urn:6th:problem:403", title: "Request refused", status: 403, detail: "A same-origin request is required." },
        { status: 403, headers: { "Content-Type": "application/problem+json", "Cache-Control": "no-store" } },
      );
    }
  }
  const origin = getApiOrigin();
  const targetUrl = new URL(request.nextUrl.pathname + request.nextUrl.search, origin);

  const forwardHeaders = new Headers();
  forwardHeaders.set("x-request-id", randomUUID());
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value) forwardHeaders.set(name, value);
  }
  const cookie = tenantCookies(request.headers.get("cookie") ?? "");
  if (cookie) forwardHeaders.set("cookie", cookie);

  let body: ArrayBuffer | undefined = undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    try {
      const buffer = await request.arrayBuffer();
      if (buffer.byteLength > 0) {
        body = buffer;
      }
    } catch {
      // Body read error
    }
  }

  try {
    const upstream = await fetch(targetUrl.toString(), {
      method: request.method,
      headers: forwardHeaders,
      body,
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
    console.error(`[API Proxy] Connection to ${targetUrl.toString()} failed:`, err);
    return NextResponse.json(
      {
        type: "urn:6th:problem:503",
        title: "Service Unavailable",
        status: 503,
        detail: "The upstream API service is temporarily unavailable.",
      },
      {
        status: 503,
        headers: {
          "Content-Type": "application/problem+json",
          "Cache-Control": "no-store",
        },
      },
    );
  }
}

export const GET = handleProxy;
export const POST = handleProxy;
export const PUT = handleProxy;
export const PATCH = handleProxy;
export const DELETE = handleProxy;
export const HEAD = handleProxy;
export const OPTIONS = handleProxy;
