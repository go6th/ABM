// The client's one door to the API (same origin through the /v1 rewrite).
// Cookie session, CSRF token on every state-changing call, problem-details errors with their
// stable type. No token is ever put in a URL, local storage or a log. A call the API refuses
// for a stale CSRF token (its ``csrf_refused`` problem) reads the session again and is replayed
// once; apiPublic() is the door for the sign-in screens, which have no session or token yet.

import { operations } from "@/lib/gen/api.gen";
import { clearUploadSessions } from "@/lib/upload-store";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly type: string,
    public readonly detail: string,
    // safe structured context some problems carry (e.g. the detected company candidates)
    public readonly context: Record<string, unknown> | null = null,
    // the whole parsed error body, for the few replies that are not problem+json (sign-in's
    // workspace choice); null when the body was not a JSON object
    public readonly body: Record<string, unknown> | null = null,
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

/** The API's problem type for a missing or stale CSRF token (``APIProblem(403, "csrf_refused")``). */
export const CSRF_REFUSED = "csrf_refused";

/** Clear session-bound client state after logout or an account/workspace change. */
export function clearSession(): void {
  csrfToken = null;
  clearUploadSessions();
}

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
  let context: Record<string, unknown> | null = null;
  let raw: Record<string, unknown> | null = null;
  try {
    const body = (await reply.json()) as { type?: string; detail?: string; context?: unknown };
    if (body && typeof body === "object") raw = body as Record<string, unknown>;
    if (typeof body.type === "string") type = body.type.replace("urn:6th:problem:", "");
    if (typeof body.detail === "string") detail = body.detail;
    if (body.context && typeof body.context === "object") context = body.context as Record<string, unknown>;
  } catch {
    // a non-JSON error body: keep the status
  }
  return new ApiError(reply.status, type, detail, context, raw);
}

/** Send a state-changing call with the session's CSRF token. When the API refuses the token
 * (stale after a sign-in elsewhere, or a restarted API), the session is read again and the
 * call replayed exactly once; a second refusal is the caller's error. */
async function withCsrf(send: (headers: Record<string, string>) => Promise<Response>, base: Record<string, string>): Promise<Response> {
  if (csrfToken === null) await session();
  const first = await send(csrfToken !== null ? { ...base, "X-CSRF-Token": csrfToken } : base);
  if (first.status !== 403) return first;
  const refusal = await problem(first.clone());
  if (refusal.type !== CSRF_REFUSED) return first;
  csrfToken = null;
  await session();
  if (csrfToken === null) return first;
  return send({ ...base, "X-CSRF-Token": csrfToken });
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
  signal?: AbortSignal,
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
  const send = (h: Record<string, string>) => fetch(path, {
    method,
    headers: h,
    body: payload,
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  const reply = SAFE.has(method.toUpperCase()) ? await send(headers) : await withCsrf(send, headers);
  return parseReply<T>(reply, operation.response);
}

async function parseReply<T>(reply: Response, schema: { parse: (value: unknown) => unknown } | null): Promise<T> {
  if (!reply.ok) throw await problem(reply);
  if (reply.status === 204 || schema === null) return undefined as T;
  try {
    return schema.parse(await reply.json()) as T;
  } catch {
    throw contractError("contract_mismatch");
  }
}

/** The sign-in screens' door: the same generated contract on the request and the reply, the
 * same problem-details errors, but no session read and no CSRF token (the caller has neither
 * yet). Only for the operations the API itself leaves open. */
export async function apiPublic<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  if (!path.startsWith("/v1/")) throw new Error("API paths start with /v1/");
  const operation = operationFor(method, path);
  if (!operation) throw new ApiError(500, "operation_not_in_contract", "This API operation is not in the generated contract.");
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload: string | undefined;
  if (body !== undefined) {
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
  const reply = await fetch(path, { method, headers, body: payload, credentials: "same-origin", cache: "no-store", signal });
  return parseReply<T>(reply, operation.response);
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
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    "Content-Type": "application/json",
  };
  const reply = await withCsrf((h) => fetch(path, {
    method,
    headers: h,
    body: JSON.stringify(payload),
    credentials: "same-origin",
    cache: "no-store",
    signal,
  }), headers);
  if (!reply.ok) throw await problem(reply);
  if (!reply.headers.get("content-type")?.includes("text/event-stream") || !reply.body) {
    throw new ApiError(502, "stream_contract_mismatch", "The API did not return a chat stream.");
  }
  return reply;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID().replaceAll("-", "");
}
