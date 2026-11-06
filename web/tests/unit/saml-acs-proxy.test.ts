// SAML ACS through the /v1 proxy (fix prompt B, task 2). The IdP's HTTP-POST binding is a
// cross-site form post: its Origin is the IdP (or "null"), and it carries no CSRF token. The proxy
// lets exactly that one request through (POST, form-encoded, at most 256 KB) and keeps refusing
// every other cross-site write. The API binds the sign-in to this browser with the pre-auth
// cookie (identity/broker._claim_state), so the auth cookie allow-list is forwarded as for every
// /v1 call; any other cookie is dropped.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST, PUT } from "@/app/v1/[[...path]]/route";
import { SAML_ACS_BODY_LIMIT, isSamlAcsPost } from "@/lib/cross-site";

const fetchMock = vi.fn();
const ACS = "https://app.example/v1/sso/saml/acs";
const FORM = "application/x-www-form-urlencoded";

beforeEach(() => {
  vi.stubEnv("API_ORIGIN", "http://api.test:8080");
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(
    new Response(null, { status: 303, headers: { location: "/", "set-cookie": "__Host-ai_session=new; Path=/; Secure; HttpOnly" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function idpPost(url: string, init: { origin?: string | null; type?: string; body?: string; cookie?: string; length?: string } = {}, method = "POST") {
  const headers: Record<string, string> = { host: "app.example", "content-type": init.type ?? FORM };
  if (init.origin !== null) headers.origin = init.origin ?? "https://idp.example.com";
  if (init.cookie) headers.cookie = init.cookie;
  if (init.length) headers["content-length"] = init.length;
  return new NextRequest(url, { method, headers, body: init.body ?? "SAMLResponse=PHNhbWw%2B&RelayState=r".padEnd(40, "x") });
}

describe("the SAML ACS allow-list", () => {
  it("forwards the IdP's cross-site form post and passes the redirect and session cookie back", async () => {
    const reply = await POST(idpPost(ACS));
    expect(reply.status).toBe(303);
    expect(reply.headers.get("location")).toBe("/");
    expect(reply.headers.get("set-cookie")).toContain("__Host-ai_session=new");
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0]! as [string, RequestInit];
    expect(url).toBe("http://api.test:8080/v1/sso/saml/acs");
    expect((init.headers as Headers).get("content-type")).toBe(FORM);
  });

  it("accepts the opaque origin some browsers send for a cross-site form post", async () => {
    expect((await POST(idpPost(ACS, { origin: "null" }))).status).toBe(303);
    expect((await POST(idpPost(ACS, { origin: null }))).status).toBe(303);
  });

  it("forwards only the auth cookies (the pre-auth cookie binds the sign-in to this browser)", async () => {
    await POST(idpPost(ACS, { cookie: "__Host-ai_preauth=p; tracker=t; __Host-ai_preauth_once=1" }));
    const sent = (fetchMock.mock.calls[0]![1] as RequestInit).headers as Headers;
    expect(sent.get("cookie")).toBe("__Host-ai_preauth=p; __Host-ai_preauth_once=1");
  });

  it("refuses anything but a form post", async () => {
    const json = await POST(idpPost(ACS, { type: "application/json", body: "{}" }));
    expect(json.status).toBe(415);
    const multipart = await POST(idpPost(ACS, { type: "multipart/form-data; boundary=x" }));
    expect(multipart.status).toBe(415);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("caps the body at 256 KB, declared or streamed", async () => {
    const declared = await POST(idpPost(ACS, { length: String(SAML_ACS_BODY_LIMIT + 1) }));
    expect(declared.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockImplementation(async (_url: string, init: RequestInit) => {
      const reader = (init.body as ReadableStream<Uint8Array>).getReader();
      for (;;) { const { done } = await reader.read(); if (done) break; }
      return new Response(null, { status: 303 });
    });
    const streamed = await POST(idpPost(ACS, { body: "SAMLResponse=" + "x".repeat(SAML_ACS_BODY_LIMIT), length: "40" }));
    expect(streamed.status).toBe(413);
  });

  it("keeps the same-origin rule for every other write, the ACS path's other methods included", async () => {
    expect((await POST(idpPost("https://app.example/v1/sso/logout"))).status).toBe(403);
    expect((await POST(idpPost("https://app.example/v1/sso/saml/acs/extra"))).status).toBe(403);
    expect((await POST(idpPost("https://app.example/v1/auth/login"))).status).toBe(403);
    expect((await PUT(idpPost(ACS, {}, "PUT"))).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("matches the one path and method exactly", () => {
    expect(isSamlAcsPost("POST", "/v1/sso/saml/acs")).toBe(true);
    expect(isSamlAcsPost("post", "/v1/sso/saml/acs")).toBe(false);
    expect(isSamlAcsPost("POST", "/v1/sso/saml/acs/")).toBe(false);
    expect(isSamlAcsPost("GET", "/v1/sso/saml/acs")).toBe(false);
    expect(isSamlAcsPost("POST", "/v1/sso/oidc/callback")).toBe(false);
  });
});
