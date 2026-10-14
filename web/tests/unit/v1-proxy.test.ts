// The /v1 proxy: which cookies reach the API (including the "Keep me signed in = off" marker),
// the body size limit before anything is forwarded, the one API origin, and a failure log that
// never carries a query string.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST, PUT } from "@/app/v1/[[...path]]/route";
import { apiOrigin } from "@/lib/api-origin";
import { bodyLimit, capBody, declaredLength, JSON_BODY_LIMIT } from "@/lib/proxy-body";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubEnv("API_ORIGIN", "http://api.test:8080");
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function post(path: string, init: { body?: string; headers?: Record<string, string> } = {}, method = "POST") {
  return new NextRequest(`https://app.example${path}`, {
    method,
    headers: { origin: "https://app.example", host: "app.example", "content-type": "application/json", ...init.headers },
    body: init.body,
  });
}

describe("cookies", () => {
  it("passes the session, preauth and preauth-once cookies and nothing else", async () => {
    const request = new NextRequest("https://app.example/v1/sso/session", {
      headers: { cookie: "__Host-ai_session=s; __Host-ai_preauth=p; __Host-ai_preauth_once=1; other=x; analytics=y" },
    });
    await GET(request);
    const sent = (fetchMock.mock.calls[0]![1] as RequestInit).headers as Headers;
    expect(sent.get("cookie")).toBe("__Host-ai_session=s; __Host-ai_preauth=p; __Host-ai_preauth_once=1");
  });
});

describe("body size", () => {
  it("refuses a declared length over the JSON limit before forwarding anything", async () => {
    const reply = await POST(post("/v1/chat/ask", { body: "{}", headers: { "content-length": String(JSON_BODY_LIMIT + 1) } }));
    expect(reply.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a small body as a stream, with the half-duplex flag", async () => {
    const reply = await POST(post("/v1/chat/ask", { body: '{"q":"hi"}' }));
    expect(reply.status).toBe(200);
    const init = fetchMock.mock.calls[0]![1] as RequestInit & { duplex?: string };
    expect(init.body).toBeInstanceOf(ReadableStream);
    expect(init.duplex).toBe("half");
  });

  it("allows the dev upload route a larger body than a JSON call", () => {
    expect(bodyLimit("PUT", "/v1/documents/abc/content")).toBeGreaterThan(JSON_BODY_LIMIT);
    expect(bodyLimit("POST", "/v1/documents/abc/content")).toBe(JSON_BODY_LIMIT);
    expect(bodyLimit("PUT", "/v1/chat/provider-key")).toBe(JSON_BODY_LIMIT);
    expect(bodyLimit("PUT", "/v1/documents/abc/content/extra")).toBe(JSON_BODY_LIMIT);
  });

  it("answers 413 when a body with no honest length runs past the limit", async () => {
    fetchMock.mockImplementation(async (_url: string, init: RequestInit) => {
      const reader = (init.body as ReadableStream<Uint8Array>).getReader();
      for (;;) { const { done } = await reader.read(); if (done) break; }
      return new Response("{}");
    });
    const big = "x".repeat(JSON_BODY_LIMIT + 10);
    const reply = await PUT(post("/v1/chat/provider-key", { body: big, headers: { "content-length": "5" } }, "PUT"));
    expect(reply.status).toBe(413);
  });

  it("parses lengths and counts bytes", async () => {
    expect(declaredLength(null)).toBeNull();
    expect(declaredLength("12")).toBe(12);
    expect(declaredLength("abc")).toBe(Number.POSITIVE_INFINITY);
    const source = new Response("0123456789").body!;
    const capped = capBody(source, 4);
    await expect(new Response(capped.stream).text()).rejects.toThrow();
    expect(capped.exceeded()).toBe(true);
  });
});

describe("failures", () => {
  it("logs the path without its query string and answers 503", async () => {
    fetchMock.mockRejectedValue(new TypeError("connect ECONNREFUSED http://api.test:8080/v1/downloads/j?token=SECRET"));
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const reply = await GET(new NextRequest("https://app.example/v1/downloads/j/parts/1?token=SECRET"));
    expect(reply.status).toBe(503);
    const logged = JSON.stringify(log.mock.calls);
    expect(logged).toContain("/v1/downloads/j/parts/1");
    expect(logged).not.toContain("SECRET");
    expect(logged).not.toContain("token=");
  });
});

describe("the API origin", () => {
  it("uses API_ORIGIN, without a trailing slash", () => {
    vi.stubEnv("API_ORIGIN", "https://api.example.com/");
    expect(apiOrigin()).toBe("https://api.example.com");
  });
  it("falls back to the local API outside production only", () => {
    vi.stubEnv("API_ORIGIN", "");
    vi.stubEnv("NODE_ENV", "development");
    expect(apiOrigin()).toBe("http://127.0.0.1:8080");
    vi.stubEnv("NODE_ENV", "production");
    expect(apiOrigin()).toBeNull();
  });
  it("fails closed in production without an API_ORIGIN: the proxy answers 503 and calls nothing", async () => {
    vi.stubEnv("API_ORIGIN", "");
    vi.stubEnv("NODE_ENV", "production");
    const reply = await GET(new NextRequest("https://app.example/v1/me"));
    expect(reply.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
