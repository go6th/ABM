// ADR-0127: the API's Cloud Run service is invoked only by the web service account, so every
// server-side call to the API carries the web server's Google ID token in
// X-Serverless-Authorization (Cloud Run IAM reads it there; Authorization stays the app's own:
// the operator proxy's staff bearer). On Cloud Run the token comes from the metadata server and is
// cached; off Cloud Run no token is fetched and no header is set.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("next/headers", () => ({
  cookies: async () => ({ toString: () => "__Host-ai_session=s", get: () => ({ value: "staff-token" }) }),
  headers: async () => new Headers({ "x-request-id": "req-1234567890" }),
}));
vi.mock("next/navigation", () => ({ redirect: (to: string) => { throw new Error(`redirect:${to}`); } }));

import { GET as v1Get, POST as v1Post } from "@/app/v1/[[...path]]/route";
import { GET as operatorGet } from "@/app/api/operator/v1/[...path]/route";
import { GET as readyz } from "@/app/readyz/route";
import { serverGet } from "@/lib/server-api";
import { clearIdTokenCache, idTokenFor, serverlessAuth } from "@/lib/serverless-auth";

const METADATA = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";
const fetchMock = vi.fn();

function jwt(expSeconds: number): string {
  const part = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${part({ alg: "RS256" })}.${part({ aud: "https://api.example.com", exp: expSeconds })}.sig`;
}

let token = "";
beforeEach(() => {
  clearIdTokenCache();
  vi.stubEnv("API_ORIGIN", "https://api.example.com");
  vi.stubEnv("K_SERVICE", "ai-staging-web");
  token = jwt(Math.floor(Date.now() / 1000) + 3600);
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string | URL) => {
    if (String(url).startsWith(METADATA)) return new Response(token, { status: 200 });
    return new Response(JSON.stringify({ notifications: [], unread: 0 }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

function upstreamCalls() {
  return fetchMock.mock.calls.filter(([url]) => !String(url).startsWith(METADATA));
}
function header(init: RequestInit, name: string): string | null {
  return new Headers(init.headers as HeadersInit).get(name);
}

describe("the ID token", () => {
  it("comes from the metadata server for the API audience, and is cached", async () => {
    expect(await idTokenFor("https://api.example.com")).toBe(token);
    expect(await idTokenFor("https://api.example.com")).toBe(token);
    const metadata = fetchMock.mock.calls.filter(([url]) => String(url).startsWith(METADATA));
    expect(metadata).toHaveLength(1);
    expect(String(metadata[0]![0])).toBe(`${METADATA}?audience=${encodeURIComponent("https://api.example.com")}`);
    expect(header(metadata[0]![1] as RequestInit, "metadata-flavor")).toBe("Google");
  });

  it("is fetched again five minutes before it expires", async () => {
    token = jwt(Math.floor(Date.now() / 1000) + 120);
    await idTokenFor("https://api.example.com");
    await idTokenFor("https://api.example.com");
    expect(fetchMock.mock.calls.filter(([url]) => String(url).startsWith(METADATA))).toHaveLength(2);
  });

  it("is never fetched off Cloud Run (dev and tests need no metadata server)", async () => {
    vi.stubEnv("K_SERVICE", "");
    expect(await serverlessAuth("https://api.example.com")).toEqual({});
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("every server-side call to the API carries it", () => {
  it("the /v1 proxy, and a browser cannot supply its own", async () => {
    await v1Get(new NextRequest("https://app.example/v1/notifications", {
      headers: { cookie: "__Host-ai_session=s", "x-serverless-authorization": "Bearer forged" },
    }));
    const [, init] = upstreamCalls()[0]!;
    expect(header(init as RequestInit, "x-serverless-authorization")).toBe(`Bearer ${token}`);
    await v1Post(new NextRequest("https://app.example/v1/auth/login", {
      method: "POST",
      headers: { origin: "https://app.example", host: "app.example", "content-type": "application/json" },
      body: "{}",
    }));
    expect(header(upstreamCalls()[1]![1] as RequestInit, "x-serverless-authorization")).toBe(`Bearer ${token}`);
  });

  it("server components (serverGet)", async () => {
    await serverGet("/v1/notifications");
    expect(header(upstreamCalls()[0]![1] as RequestInit, "x-serverless-authorization")).toBe(`Bearer ${token}`);
  });

  it("the operator proxy, keeping the staff bearer in Authorization", async () => {
    vi.stubEnv("NODE_ENV", "production");
    await operatorGet(new NextRequest("https://staff.example/api/operator/v1/console/clients"));
    const init = upstreamCalls()[0]![1] as RequestInit;
    expect(header(init, "x-serverless-authorization")).toBe(`Bearer ${token}`);
    expect(header(init, "authorization")).toBe("Bearer staff-token");
  });

  it("the readiness check", async () => {
    await readyz();
    expect(header(upstreamCalls()[0]![1] as RequestInit, "x-serverless-authorization")).toBe(`Bearer ${token}`);
  });

  it("fails the call closed when the metadata server does not answer on Cloud Run", async () => {
    fetchMock.mockImplementation(async (url: string | URL) => {
      if (String(url).startsWith(METADATA)) return new Response("", { status: 500 });
      return new Response("{}", { status: 200 });
    });
    const reply = await readyz();
    expect(reply.status).toBe(503);
    expect(upstreamCalls()).toHaveLength(0);
  });
});
