// Server Components' door: only the tenant cookies go upstream, every call has a deadline, and a
// call that runs past it fails as a 504 ServerApiError (rendered by the pages' unavailable state).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let cookieHeader = "";
vi.mock("next/headers", () => ({
  cookies: async () => ({ toString: () => cookieHeader }),
  headers: async () => new Headers({ "x-request-id": "req-1234567890" }),
}));
vi.mock("next/navigation", () => ({ redirect: (to: string) => { throw new Error(`redirect:${to}`); } }));

import { REQUEST_TIMEOUT_MS, serverGet, ServerApiError } from "@/lib/server-api";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubEnv("API_ORIGIN", "http://api.test:8080");
  cookieHeader = "theme=dark; __Host-ai_session=s; ga=1";
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ notifications: [], unread: 0 }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("serverGet", () => {
  it("forwards only the tenant cookies, with a timeout signal", async () => {
    await serverGet("/v1/notifications");
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect((init.headers as Record<string, string>).cookie).toBe("__Host-ai_session=s");
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(REQUEST_TIMEOUT_MS).toBe(10_000);
  });

  it("sends the user to sign in when no tenant cookie is present, even with other cookies", async () => {
    cookieHeader = "theme=dark";
    await expect(serverGet("/v1/notifications", "/here")).rejects.toThrow("redirect:/login?next=%2Fhere");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("maps a timed-out call to a 504 ServerApiError and any other failure to 503", async () => {
    fetchMock.mockRejectedValueOnce(new DOMException("The operation was aborted due to timeout", "TimeoutError"));
    const timedOut = await serverGet("/v1/notifications").catch((e: unknown) => e);
    expect(timedOut).toBeInstanceOf(ServerApiError);
    expect((timedOut as ServerApiError).status).toBe(504);
    expect((timedOut as ServerApiError).reference).toBe("req-1234");

    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    const down = await serverGet("/v1/notifications").catch((e: unknown) => e);
    expect((down as ServerApiError).status).toBe(503);
  });
});
