// The client's one door to the API (same origin through the /v1 rewrite).
// Cookie session, CSRF token on every state-changing call, problem-details errors with their
// stable type. No token is ever put in a URL, local storage or a log.

import { operations } from "@/lib/gen/api.gen";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly type: string,
    public readonly detail: string,
  ) {
    super(`${status} ${type}`);
  }
}

export interface Session {
  user_id: string;
  role: "admin" | "analyst" | "seller" | "viewer";
  credential: string;
  step_up_fresh: boolean;
  csrf_token?: string;
}

let csrfToken: string | null = null;

export async function session(): Promise<Session | null> {
  try {
    const body = await api<Session>("GET", "/v1/sso/session");
    csrfToken = body.csrf_token ?? null;
    return body;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

async function problem(reply: Response): Promise<ApiError> {
  let type = `http_${reply.status}`;
  let detail = reply.statusText;
  try {
    const body = (await reply.json()) as { type?: string; detail?: string };
    if (typeof body.type === "string") type = body.type.replace("urn:6th:problem:", "");
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // a non-JSON error body: keep the status
  }
  return new ApiError(reply.status, type, detail);
}

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

function routeMatches(template: string, actual: string): boolean {
  const pattern = template
    .split("/")
    .map((part) => part.startsWith("{") && part.endsWith("}")
      ? "[^/]+"
      : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("/");
  return new RegExp(`^${pattern}$`).test(actual);
}

function operationFor(method: string, path: string) {
  const pathname = new URL(path, "http://same-origin.invalid").pathname;
  return Object.values(operations).find((operation) =>
    operation.plane === "tenant" &&
    operation.method === method.toUpperCase() &&
    routeMatches(operation.path, pathname),
  );
}

function contractError(type: string): ApiError {
  return new ApiError(502, type, "The API response did not match its generated contract.");
}

export async function api<T>(
  method: string,
  path: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  if (!path.startsWith("/v1/")) throw new Error("API paths start with /v1/");
  const operation = operationFor(method, path);
  if (!operation) throw new ApiError(500, "operation_not_in_contract", "This API operation is not in the generated contract.");
  const headers: Record<string, string> = { Accept: "application/json", ...extraHeaders };
  let payload: BodyInit | undefined;
  if (body instanceof Blob) {
    payload = body;
  } else if (body !== undefined) {
    if (operation.request) {
      try {
        body = operation.request.parse(body);
      } catch {
        throw new ApiError(0, "request_contract_mismatch", "The request did not match its API contract.");
      }
    }
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  } else if (operation.requestRequired) {
    throw new ApiError(0, "request_contract_mismatch", "This API operation requires a request body.");
  }
  if (!SAFE.has(method.toUpperCase())) {
    if (csrfToken === null) await session();
    if (csrfToken !== null) headers["X-CSRF-Token"] = csrfToken;
  }
  const reply = await fetch(path, {
    method,
    headers,
    body: payload,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!reply.ok) throw await problem(reply);
  if (reply.status === 204 || operation.response === null) return undefined as T;
  try {
    return operation.response.parse(await reply.json()) as T;
  } catch {
    throw contractError("contract_mismatch");
  }
}

/** POST a first-party streaming operation with the same contract and CSRF checks as api(). */
export async function apiStream(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  if (!path.startsWith("/v1/")) throw new Error("API paths start with /v1/");
  const method = "POST";
  const operation = operationFor(method, path);
  if (!operation) throw new ApiError(500, "operation_not_in_contract", "This API operation is not in the generated contract.");
  let payload: unknown = body;
  if (operation.request) {
    try {
      payload = operation.request.parse(body);
    } catch {
      throw new ApiError(0, "request_contract_mismatch", "The request did not match its API contract.");
    }
  } else if (operation.requestRequired) {
    throw new ApiError(0, "request_contract_mismatch", "This API operation requires a request body.");
  }
  if (csrfToken === null) await session();
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    "Content-Type": "application/json",
  };
  if (csrfToken !== null) headers["X-CSRF-Token"] = csrfToken;
  const reply = await fetch(path, {
    method,
    headers,
    body: JSON.stringify(payload),
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  if (!reply.ok) throw await problem(reply);
  if (!reply.headers.get("content-type")?.includes("text/event-stream") || !reply.body) {
    throw new ApiError(502, "stream_contract_mismatch", "The API did not return a chat stream.");
  }
  return reply;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID().replaceAll("-", "");
}
