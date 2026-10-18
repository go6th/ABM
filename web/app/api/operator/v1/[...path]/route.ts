import { randomUUID } from "node:crypto";
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { operations } from "@/lib/gen/api.gen";
import { googleOperatorOidcConfig, operatorApiOrigin, operatorOAuthCookieNames } from "@/lib/operator-oidc";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED_HEADERS = ["accept", "content-type", "idempotency-key", "x-support-grant"] as const;

function routeMatches(template: string, actual: string): boolean {
  const pattern = template
    .split("/")
    .map((part) => part.startsWith("{") && part.endsWith("}") ? "[^/]+" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("/");
  return new RegExp(`^${pattern}$`).test(actual);
}

function isOperation(method: string, path: string): boolean {
  return Object.values(operations).some(
    (operation) => operation.plane === "operator" && operation.method === method && routeMatches(operation.path, path),
  );
}

function problem(status: number, type: string, detail: string): NextResponse {
  return NextResponse.json(
    { type: `urn:6th:problem:${type}`, title: "Operator request refused", status, detail },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  const protocol = request.headers.get("x-forwarded-proto")?.split(",", 1)[0]?.trim() ?? new URL(request.url).protocol.slice(0, -1);
  if (!origin || !host || !protocol) return false;
  try {
    const supplied = new URL(origin);
    return supplied.origin === origin && supplied.host === host && supplied.protocol === `${protocol}:`;
  } catch {
    return false;
  }
}

async function proxy(request: NextRequest): Promise<NextResponse> {
  const method = request.method.toUpperCase();
  const apiPath = new URL(request.url).pathname.slice("/api".length);
  if (!isOperation(method, apiPath)) return problem(404, "not_found", "Not found.");
  const apiOrigin = operatorApiOrigin();
  if (method !== "GET" && method !== "HEAD" && !sameOrigin(request)) {
    return problem(403, "origin_refused", "The request origin could not be verified.");
  }

  const secure = googleOperatorOidcConfig()?.secureCookies ?? process.env.NODE_ENV === "production";
  const token = (await cookies()).get(operatorOAuthCookieNames(secure).session)?.value;
  if (!token) return problem(401, "operator_session_required", "Staff sign-in is required.");

  const headers = new Headers();
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("x-request-id", randomUUID());
  for (const name of ALLOWED_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  let body: string | undefined;
  if (method !== "GET" && method !== "HEAD") {
    const size = Number(request.headers.get("content-length") ?? "0");
    if (Number.isFinite(size) && size > 1_000_000) return problem(413, "body_too_large", "Request body is too large.");
    const contentType = request.headers.get("content-type") ?? "";
    if (!contentType.toLowerCase().startsWith("application/json")) {
      return problem(415, "content_type_refused", "JSON is required for this operation.");
    }
    body = await request.text();
    if (body.length > 1_000_000) return problem(413, "body_too_large", "Request body is too large.");
  }

  if (apiOrigin) {
    try {
      const upstream = await fetch(new URL(`${apiPath}${new URL(request.url).search}`, apiOrigin), {
        method,
        headers,
        body,
        cache: "no-store",
        redirect: "manual",
      });

      if (upstream.ok || (upstream.status !== 503 && upstream.status !== 502)) {
        const outputHeaders = new Headers({ "Cache-Control": "no-store" });
        for (const name of ["content-type", "etag", "retry-after", "x-request-id", "x-support-view"] as const) {
          const value = upstream.headers.get(name);
          if (value) outputHeaders.set(name, value);
        }
        return new NextResponse(upstream.status === 204 ? null : await upstream.arrayBuffer(), {
          status: upstream.status,
          headers: outputHeaders,
        });
      }
    } catch {
      // Upstream failures are returned as 503 below; never fabricate operator state.
    }
  }

  return problem(503, "operator_api_unavailable", "The operator API is temporarily unavailable.");
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
