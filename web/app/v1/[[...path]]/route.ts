import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const HOP_BY_HOP_HEADERS = new Set([
  "host",
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

function getApiOrigin(): string {
  if (process.env.API_ORIGIN) {
    return process.env.API_ORIGIN.replace(/\/$/, "");
  }
  return process.env.NODE_ENV === "production" ? "http://api:8080" : "http://127.0.0.1:8080";
}

async function handleProxy(request: NextRequest): Promise<NextResponse> {
  const origin = getApiOrigin();
  const targetUrl = new URL(request.nextUrl.pathname + request.nextUrl.search, origin);

  const forwardHeaders = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
      forwardHeaders.set(key, value);
    }
  });

  const clientIp = request.headers.get("x-forwarded-for");
  if (clientIp) {
    forwardHeaders.set("x-forwarded-for", clientIp);
  }
  forwardHeaders.set("x-forwarded-proto", request.nextUrl.protocol.replace(":", ""));
  forwardHeaders.set("x-forwarded-host", request.nextUrl.host);

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
