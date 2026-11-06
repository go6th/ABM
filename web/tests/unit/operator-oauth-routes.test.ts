import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET as startLogin } from "@/app/api/operator/login/route";
import { GET as finishLogin } from "@/app/api/operator/callback/route";
import { operatorOAuthCookieNames } from "@/lib/operator-oidc";

function configure() {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("API_ORIGIN", "https://api.example.com");
  vi.stubEnv("OPERATOR_GOOGLE_CLIENT_ID", "client-id");
  vi.stubEnv("OPERATOR_GOOGLE_CLIENT_SECRET", "server-secret");
  vi.stubEnv("OPERATOR_GOOGLE_REDIRECT_URI", "https://staff.example.com/api/operator/callback");
  vi.stubEnv("STAFF_WORKSPACE_DOMAIN", "example.com");
}

describe("Google Workspace operator authorization-code flow", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("starts with state, nonce, PKCE, Workspace restriction and requested MFA claims", async () => {
    configure();
    const response = await startLogin(new NextRequest("https://staff.example.com/api/operator/login?return_to=%2Foperator%2Ftenants"));
    expect(response.status).toBe(302);
    const authorization = new URL(response.headers.get("location") ?? "");
    expect(authorization.origin).toBe("https://accounts.google.com");
    expect(authorization.searchParams.get("redirect_uri")).toBe("https://staff.example.com/api/operator/callback");
    expect(authorization.searchParams.get("hd")).toBe("example.com");
    expect(authorization.searchParams.get("scope")).toBe("openid email");
    expect(authorization.searchParams.get("code_challenge_method")).toBe("S256");
    expect(authorization.searchParams.get("code_challenge")).toBeTruthy();
    expect(authorization.searchParams.get("state")).toBeTruthy();
    expect(authorization.searchParams.get("nonce")).toBeTruthy();
    expect(authorization.searchParams.get("max_age")).toBe("600");
    expect(JSON.parse(authorization.searchParams.get("claims") ?? "{}")).toEqual({
      id_token: { amr: { essential: true }, auth_time: { essential: true } },
    });
    const cookieNames = response.cookies.getAll().map((cookie) => cookie.name);
    expect(cookieNames).toContain("__Host-6th_operator_oauth_state");
    expect(response.cookies.getAll().every((cookie) => cookie.httpOnly && cookie.secure)).toBe(true);
  });

  it("rejects a mismatched OAuth state before exchanging the code", async () => {
    configure();
    const names = operatorOAuthCookieNames(true);
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const request = new NextRequest("https://staff.example.com/api/operator/callback?code=code&state=wrong&iss=https%3A%2F%2Faccounts.google.com", {
      headers: { cookie: [
        names.state + "=expected",
        names.nonce + "=nonce",
        names.verifier + "=verifier",
        names.returnTo + "=%2Foperator",
      ].join("; ") },
    });
    const response = await finishLogin(request);
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toContain("error=invalid_callback");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("stores an opaque console session, never the Google ID token, after nonce and backend verification", async () => {
    configure();
    const nonce = "expected-nonce";
    const token = "header." + Buffer.from(JSON.stringify({
      nonce,
      exp: Math.floor(Date.now() / 1000) + 3600,
    })).toString("base64url") + ".signature";
    const names = operatorOAuthCookieNames(true);
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id_token: token }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        staff_id: "google-subject",
        role: "operator",
        mfa_fresh: true,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        token: "ops_opaque-session-token-0123456789abcdefghijklmnopq",
        expires_at: "2026-11-03T20:00:00Z",
        idle_timeout_s: 1800,
      }), { status: 201 }));
    vi.stubGlobal("fetch", fetch);
    const request = new NextRequest("https://staff.example.com/api/operator/callback?code=one-time-code&state=matching-state&iss=https%3A%2F%2Faccounts.google.com", {
      headers: { cookie: [
        names.state + "=matching-state",
        names.nonce + "=" + nonce,
        names.verifier + "=pkce-verifier",
        names.returnTo + "=%2Foperator%2Ftenants",
      ].join("; ") },
    });
    const response = await finishLogin(request);
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("https://staff.example.com/operator/tenants");
    const sessionCookie = response.cookies.get(names.session);
    expect(sessionCookie?.value).toBe("ops_opaque-session-token-0123456789abcdefghijklmnopq");
    expect(sessionCookie?.value).not.toContain(token);
    expect(sessionCookie?.httpOnly).toBe(true);
    expect(sessionCookie?.secure).toBe(true);
    expect(sessionCookie?.sameSite).toBe("strict");
    expect(sessionCookie?.expires).toEqual(new Date("2026-11-03T20:00:00Z"));
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(fetch.mock.calls[1]?.[1]).toMatchObject({
      headers: { Authorization: "Bearer " + token },
      cache: "no-store",
    });
    // the ID token is traded once, server side, for the opaque session
    expect(String(fetch.mock.calls[2]?.[0])).toBe("https://api.example.com/operator/v1/sessions");
    expect(fetch.mock.calls[2]?.[1]).toMatchObject({
      method: "POST",
      headers: { Authorization: "Bearer " + token },
      cache: "no-store",
    });
  });

  it("sets no cookie when the session cannot be minted", async () => {
    configure();
    const nonce = "expected-nonce";
    const token = "header." + Buffer.from(JSON.stringify({ nonce })).toString("base64url") + ".signature";
    const names = operatorOAuthCookieNames(true);
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id_token: token }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ staff_id: "s", role: "operator", mfa_fresh: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response("{}", { status: 503 }));
    vi.stubGlobal("fetch", fetch);
    const request = new NextRequest("https://staff.example.com/api/operator/callback?code=c&state=s&iss=https%3A%2F%2Faccounts.google.com", {
      headers: { cookie: [names.state + "=s", names.nonce + "=" + nonce, names.verifier + "=v"].join("; ") },
    });
    const response = await finishLogin(request);
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toContain("error=session_refused");
    expect(response.cookies.get(names.session)?.value ?? "").toBe("");
  });
});
