// One cookie allow-list for every server-side door to the API: the browser proxy, Server
// Components and the logout route forward the tenant cookies and nothing else.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { tenantCookies } from "@/lib/tenant-cookies";
import { POST as logout } from "@/app/api/auth/logout/route";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubEnv("API_ORIGIN", "http://api.test:8080");
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ csrf_token: "c" }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("tenantCookies", () => {
  it("keeps the session, preauth and preauth-once cookies in order and drops the rest", () => {
    expect(tenantCookies("analytics=y; __Host-ai_preauth_once=1; other=x; __Host-ai_session=s=t; __Host-ai_preauth=p"))
      .toBe("__Host-ai_preauth_once=1; __Host-ai_session=s=t; __Host-ai_preauth=p");
  });
  it("is empty for no header, and for only foreign cookies", () => {
    expect(tenantCookies("")).toBe("");
    expect(tenantCookies("a=1; b=2")).toBe("");
    expect(tenantCookies("__Host-ai_sessionx=1")).toBe("");
  });
});

describe("logout route", () => {
  it("forwards only the tenant cookies to the session read and the sign-out", async () => {
    const request = new NextRequest("https://app.example/api/auth/logout", {
      method: "POST",
      headers: { origin: "https://app.example", host: "app.example", cookie: "ga=1; __Host-ai_session=s; theme=dark" },
    });
    const reply = await logout(request);
    expect(reply.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const call of fetchMock.mock.calls) {
      const headers = (call[1] as RequestInit).headers as Record<string, string>;
      expect(headers.cookie).toBe("__Host-ai_session=s");
    }
  });
});
