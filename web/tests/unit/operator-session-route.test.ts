// /api/operator/session (fix prompt B, task 3; ADR-0111): a staff sign-in posted to the web tier is
// verified, then traded once for an opaque console session; the cookie holds that session token
// and never the posted IdP token. Sign-out revokes the session upstream and clears the cookie.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

let cookieValue: string | undefined = "ops_current-session-token-0123456789abcdefghijklmn";
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => (cookieValue === undefined ? undefined : { value: cookieValue }) }),
}));

import { DELETE, POST } from "@/app/api/operator/session/route";

const fetchMock = vi.fn();
const IDP_TOKEN = "idp-token-0123456789abcdef";
const SESSION = "ops_minted-session-token-0123456789abcdefghijklmnop";

beforeEach(() => {
  vi.stubEnv("API_ORIGIN", "https://api.example.com");
  vi.stubEnv("NODE_ENV", "production");
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); cookieValue = "ops_current-session-token-0123456789abcdefghijklmn"; });

function signIn(body: unknown) {
  return new NextRequest("https://staff.example.com/api/operator/session", {
    method: "POST",
    headers: { origin: "https://staff.example.com", host: "staff.example.com", "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("staff sign-in through the web tier", () => {
  it("stores the minted session token, not the IdP token", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ staff_id: "s", role: "operator", mfa_fresh: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: SESSION, expires_at: "2026-11-03T20:00:00Z", idle_timeout_s: 1800 }), { status: 201 }));
    const reply = await POST(signIn({ access_token: IDP_TOKEN }));
    expect(reply.status).toBe(200);
    const cookie = reply.cookies.get("__Host-operator_session") ?? [...reply.cookies.getAll()][0];
    expect(cookie?.value).toBe(SESSION);
    expect(cookie?.value).not.toContain(IDP_TOKEN);
    expect(cookie?.expires).toEqual(new Date("2026-11-03T20:00:00Z"));
    expect(String(fetchMock.mock.calls[1]![0])).toBe("https://api.example.com/operator/v1/sessions");
  });

  it("refuses and sets no cookie when the API will not mint a session", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ staff_id: "s", role: "operator", mfa_fresh: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response("{}", { status: 403 }));
    const reply = await POST(signIn({ access_token: IDP_TOKEN }));
    expect(reply.status).toBe(401);
    expect(reply.cookies.getAll().filter((c) => c.value)).toEqual([]);
  });

  it("refuses a minted token of the wrong shape", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ staff_id: "s", role: "operator", mfa_fresh: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: IDP_TOKEN, expires_at: "2026-11-03T20:00:00Z" }), { status: 201 }));
    expect((await POST(signIn({ access_token: IDP_TOKEN }))).status).toBe(401);
  });
});

describe("staff sign-out", () => {
  it("revokes the session upstream and clears the cookie", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const reply = await DELETE(new NextRequest("https://staff.example.com/api/operator/session", {
      method: "DELETE",
      headers: { origin: "https://staff.example.com", host: "staff.example.com" },
    }));
    expect(reply.status).toBe(200);
    expect(String(fetchMock.mock.calls[0]![0])).toBe("https://api.example.com/operator/v1/sessions/current");
    expect(fetchMock.mock.calls[0]![1]).toMatchObject({ method: "DELETE", headers: { Authorization: "Bearer ops_current-session-token-0123456789abcdefghijklmn" } });
    expect(reply.cookies.getAll().every((c) => c.value === "")).toBe(true);
  });

  it("still clears the cookie when there is no session to revoke", async () => {
    cookieValue = undefined;
    const reply = await DELETE(new NextRequest("https://staff.example.com/api/operator/session", {
      method: "DELETE",
      headers: { origin: "https://staff.example.com", host: "staff.example.com" },
    }));
    expect(reply.status).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
