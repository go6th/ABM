import { ApiError } from "@/lib/api";
import {
  operations,
  type OperatorOperation,
  type ReplyOf,
} from "@/lib/gen/api.gen";

export interface OperatorRequestOptions {
  params?: Record<string, string>;
  query?: Record<string, string | number | boolean | null | undefined>;
  body?: unknown;
  /** A file sent as the request body (the 6th Library upload); replaces ``body``. */
  file?: Blob;
  headers?: Record<string, string>;
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function pathFor(template: string, params: Record<string, string> = {}): string {
  const path = template.replace(/\{([a-zA-Z0-9_]+)\}/g, (_match, name: string) => {
    const value = params[name];
    if (value === undefined || value.length === 0) throw new Error(`missing path parameter: ${name}`);
    return encodeURIComponent(value);
  });
  if (path.includes("{")) throw new Error("unresolved API path parameter");
  return path;
}

function queryFor(query: OperatorRequestOptions["query"]): string {
  const values = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) values.set(key, String(value));
  }
  const serialized = values.toString();
  return serialized ? `?${serialized}` : "";
}

async function apiError(response: Response): Promise<ApiError> {
  let type = `http_${response.status}`;
  try {
    const payload: unknown = await response.json();
    if (typeof payload === "object" && payload !== null && "type" in payload) {
      const candidate = (payload as { type?: unknown }).type;
      if (typeof candidate === "string" && /^[a-zA-Z0-9_:.-]{1,120}$/.test(candidate)) {
        type = candidate.replace("urn:6th:problem:", "");
      }
    }
  } catch {
    // Keep the status-only code for non-JSON errors.
  }
  const detail = response.status === 401
    ? "Staff sign-in is required."
    : response.status === 403
      ? "This staff account cannot access this resource."
      : response.status === 503
        ? "The operator service is not configured or is temporarily unavailable."
        : "The operator request could not be completed.";
  return new ApiError(response.status, type, detail);
}

/** Call one generated operator operation and validate every JSON response at runtime. */
export async function operatorRequest<K extends OperatorOperation>(
  operationKey: K,
  options: OperatorRequestOptions = {},
): Promise<ReplyOf<K>> {
  const operation = operations[operationKey];
  if (operation.plane !== "operator") throw new Error("operator client refused a tenant operation");
  const path = pathFor(operation.path, options.params);
  if (!path.startsWith("/operator/v1/")) throw new Error("operator client refused a non-operator path");
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  let body: BodyInit | undefined;
  if (options.file !== undefined) {
    headers["Content-Type"] = "application/octet-stream";
    body = options.file;
  } else if (options.body !== undefined || operation.method !== "GET") {
    // the console proxy accepts only JSON on writes, so an action with no fields (New invite
    // link, approve, re-enable) sends an empty object rather than no body (it was a 415)
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body ?? {});
  }
  const response = await fetch(`/api${path}${queryFor(options.query)}`, {
    method: operation.method,
    headers,
    body,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw await apiError(response);
  if (response.status === 204 || operation.response === null) return undefined as ReplyOf<K>;
  try {
    const payload: unknown = await response.json();
    return operation.response.parse(payload) as ReplyOf<K>;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(502, "contract_mismatch", "The operator response did not match its API contract.");
  }
}

export function operatorMethodIsSafe(method: string): boolean {
  return SAFE_METHODS.has(method.toUpperCase());
}
