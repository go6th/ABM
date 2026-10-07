// The client's one door to the API (same origin through the /v1 rewrite).
// Cookie session, CSRF token on every state-changing call, problem-details errors with their
// stable type. No token is ever put in a URL, local storage or a log.

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
  const reply = await fetch("/v1/sso/session", { credentials: "same-origin", cache: "no-store" });
  if (reply.status === 401) return null;
  if (!reply.ok) throw await problem(reply);
  const body = (await reply.json()) as Session;
  csrfToken = body.csrf_token ?? null;
  return body;
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

export async function api<T>(
  method: string,
  path: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  if (!path.startsWith("/v1/")) throw new Error("API paths start with /v1/");
  const headers: Record<string, string> = { Accept: "application/json", ...extraHeaders };
  if (!SAFE.has(method.toUpperCase())) {
    if (csrfToken === null) await session();
    if (csrfToken !== null) headers["X-CSRF-Token"] = csrfToken;
  }
  let payload: BodyInit | undefined;
  if (body instanceof Blob) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const reply = await fetch(path, {
    method,
    headers,
    body: payload,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!reply.ok) throw await problem(reply);
  if (reply.status === 204) return undefined as T;
  return (await reply.json()) as T;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID().replaceAll("-", "");
}
