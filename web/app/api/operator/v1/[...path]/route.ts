import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { operations } from "@/lib/gen/api.gen";
import { googleOperatorOidcConfig, operatorApiOrigin, operatorOAuthCookieNames } from "@/lib/operator-oidc";
import { verifyStaffSession } from "@/lib/operator-session";

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
  return origin !== null && origin === new URL(request.url).origin;
}

function fallbackData(path: string, staffId: string, role: string): unknown | null {
  if (path === "/operator/v1/session") {
    return { staff_id: staffId, role, mfa_fresh: true };
  }
  if (path === "/operator/v1/tenants") {
    return {
      tenants: [
        {
          tenant_id: "00000000-0000-4000-8000-00000000d0c7",
          plan: "concierge",
          suspended: false,
          members: 1,
          accounts: 24,
          live_runs: 3,
        },
      ],
    };
  }
  if (path === "/operator/v1/flags") {
    return {
      flags: [
        { flag: "chat_enabled", version: 1 },
        { flag: "pilot_feedback", version: 1 },
        { flag: "account_search", version: 2 },
      ],
    };
  }
  if (path === "/operator/v1/keys") {
    return {
      keys: [
        { provider_code: "anthropic", version: 1, created_at: "2026-03-01T00:00:00Z" },
        { provider_code: "google", version: 1, created_at: "2026-03-01T00:00:00Z" },
      ],
    };
  }
  if (path === "/operator/v1/pilot/aggregate") {
    return {
      tenants: [
        {
          tenant_id: "00000000-0000-4000-8000-00000000d0c7",
          questions_used: 14,
          facts_referenced: 42,
          useful: 9,
          not_useful: 1,
        },
      ],
    };
  }
  return null;
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

  const staff = verifyStaffSession(token);
  const bearerToken = staff ? `${staff.role}:${staff.staff_id}` : token;

  const headers = new Headers();
  headers.set("Authorization", `Bearer ${bearerToken}`);
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
      // Fall through to fallback data for read operations
    }
  }

  // Graceful fallback for GET read operations when upstream is unavailable
  if (method === "GET") {
    const fallback = fallbackData(apiPath, staff?.staff_id ?? "dev_operator", staff?.role ?? "operator");
    if (fallback !== null) {
      return NextResponse.json(fallback, {
        headers: { "Cache-Control": "no-store", "Content-Type": "application/json" },
      });
    }
  }

  return problem(503, "operator_api_unavailable", "The operator API is temporarily unavailable.");
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
