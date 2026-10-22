// The operator proxy streams bodies both ways: a request body passes through counted and cut
// off at its limit (no missing content-length is taken as zero), and the API's reply body is
// passed on as it arrives rather than held in memory.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => ({ value: "staff-token" }) }) }));

import { GET, POST } from "@/app/api/operator/v1/[...path]/route";
import { JSON_BODY_LIMIT } from "@/lib/proxy-body";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("API_ORIGIN", "https://api.example.com");
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

function post(path: string, body: string | undefined, headers: Record<string, string> = {}) {
  return new NextRequest(`https://staff.example${path}`, {
    method: "POST",
    headers: { origin: "https://staff.example", host: "staff.example", "content-type": "application/json", ...headers },
    body,
  });
}

describe("request bodies", () => {
  it("streams a JSON body with the half-duplex flag instead of buffering it", async () => {
    const reply = await POST(post("/api/operator/v1/console/clients", '{"name":"Acme"}'));
    expect(reply.status).toBe(200);
    const init = fetchMock.mock.calls[0]![1] as RequestInit & { duplex?: string };
    expect(init.body).toBeInstanceOf(ReadableStream);
    expect(init.duplex).toBe("half");
    expect(await new Response(init.body as ReadableStream<Uint8Array>).text()).toBe('{"name":"Acme"}');
  });

  it("refuses a declared length over the limit before forwarding anything", async () => {
    const reply = await POST(post("/api/operator/v1/console/clients", "{}", { "content-length": String(JSON_BODY_LIMIT + 1) }));
    expect(reply.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("cuts off a body with no or a false length once it runs past the limit (413, not 0)", async () => {
    fetchMock.mockImplementation(async (_url: URL, init: RequestInit) => {
      const reader = (init.body as ReadableStream<Uint8Array>).getReader();
      for (;;) { const { done } = await reader.read(); if (done) break; }
      return new Response("{}");
    });
    const reply = await POST(post("/api/operator/v1/console/clients", "x".repeat(JSON_BODY_LIMIT + 10), { "content-length": "5" }));
    expect(reply.status).toBe(413);
  });

  it("still requires the file itself for the one binary upload", async () => {
    const reply = await POST(post("/api/operator/v1/library/uploads", "{}"));
    expect(reply.status).toBe(415);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("reply bodies", () => {
  it("passes the API's body through as a stream with the allowed headers", async () => {
    fetchMock.mockResolvedValue(new Response('{"clients":[]}', { status: 200, headers: { "content-type": "application/json", etag: "\"v1\"", "x-secret": "no" } }));
    const reply = await GET(new NextRequest("https://staff.example/api/operator/v1/console/clients"));
    expect(reply.status).toBe(200);
    expect(reply.headers.get("etag")).toBe("\"v1\"");
    expect(reply.headers.get("x-secret")).toBeNull();
    expect(await reply.text()).toBe('{"clients":[]}');
  });

  it("answers 204 with no body", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const reply = await GET(new NextRequest("https://staff.example/api/operator/v1/console/clients"));
    expect(reply.status).toBe(204);
    expect(reply.body).toBeNull();
  });
});
