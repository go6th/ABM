// The one error type the screens see (F8): a status, a stable problem type, a safe detail and the
// request id the API returned, so every error state can show it. Never carries a body or a token.
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly type: string,
    public readonly detail: string,
    public readonly requestId: string | null = null,
  ) {
    super(`${status} ${type}`);
    this.name = "ApiError";
  }

  get forbidden(): boolean { return this.status === 403; }
  get unauthenticated(): boolean { return this.status === 401; }
  get notFound(): boolean { return this.status === 404; }
}

export function asApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  if (e instanceof DOMException && e.name === "AbortError") return new ApiError(0, "aborted", "The request was cancelled.");
  return new ApiError(0, "network", "The service could not be reached.");
}

/** The problem-details body as the API sends it (application/problem+json). */
export async function problemOf(reply: Response): Promise<ApiError> {
  let type = `http_${reply.status}`;
  let detail = reply.statusText || "The request failed.";
  let requestId = reply.headers.get("X-Request-ID");
  try {
    const body = (await reply.json()) as { type?: unknown; detail?: unknown; request_id?: unknown };
    if (typeof body.type === "string") type = body.type.replace("urn:6th:problem:", "");
    if (typeof body.detail === "string") detail = body.detail.slice(0, 300);
    if (typeof body.request_id === "string") requestId = body.request_id;
  } catch {
    // a non-JSON error body: keep the status
  }
  return new ApiError(reply.status, type, detail, requestId);
}
