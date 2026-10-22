// Server Components' door to the API. Reads the session cookie with cookies(), forwards it and a
// request id server to server, validates the reply against the generated contract, and never
// hands the cookie to client code. A 401 sends the user to sign in with a return path. Only the
// tenant cookies go upstream, and a call that does not answer within REQUEST_TIMEOUT_MS fails as
// a 504 ServerApiError, which the pages render as their unavailable state.
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { apiOrigin } from "@/lib/api-origin";
import { operations } from "@/lib/gen/api.gen";
import { tenantCookies } from "@/lib/tenant-cookies";

export const REQUEST_TIMEOUT_MS = 10_000;

export class ServerApiError extends Error {
  constructor(public readonly status: number, public readonly reference: string | null) {
    super(`API ${status}`);
  }
}

function contractFor(path: string) {
  const pathname = path.split("?")[0] ?? path;
  return Object.values(operations).find((op) => {
    if (op.plane !== "tenant" || op.method !== "GET") return false;
    const pattern = op.path.split("/").map((p) => (p.startsWith("{") ? "[^/]+" : p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))).join("/");
    return new RegExp(`^${pattern}$`).test(pathname);
  });
}

/** GET a tenant operation. Returns null on 404 (a missing and a foreign resource look the same)
 * and on any status listed in emptyOn, such as 409 for "no finished run yet". */
export async function serverGet<T>(path: string, returnTo = "/", emptyOn: number[] = []): Promise<T | null> {
  const op = contractFor(path);
  if (!op) throw new Error("This API operation is not in the generated contract.");
  const cookie = tenantCookies((await cookies()).toString());
  if (!cookie) redirect(`/login?next=${encodeURIComponent(returnTo)}`);
  const requestId = (await headers()).get("x-request-id") ?? crypto.randomUUID();
  const origin = apiOrigin();
  if (!origin) throw new ServerApiError(503, requestId.slice(0, 8));
  let reply: Response;
  try {
    reply = await fetch(`${origin}${path}`, {
      headers: { cookie, accept: "application/json", "x-request-id": requestId },
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    throw new ServerApiError(timedOut ? 504 : 503, requestId.slice(0, 8));
  }
  if (reply.status === 401) redirect(`/login?next=${encodeURIComponent(returnTo)}`);
  if (reply.status === 404 || emptyOn.includes(reply.status)) return null;
  if (!reply.ok) throw new ServerApiError(reply.status, (reply.headers.get("x-request-id") ?? requestId).slice(0, 8));
  const body: unknown = await reply.json();
  return (op.response ? op.response.parse(body) : body) as T;
}
