// The headers every response carries (next.config.ts) and the per-request nonce CSP (proxy.ts).
import { afterEach, expect, test, vi } from "vitest";
import { NextRequest } from "next/server";
import config from "@/next.config";
import { proxy } from "@/proxy";

afterEach(() => vi.unstubAllEnvs());

async function configured(): Promise<Record<string, string>> {
  const rules = await config.headers!();
  return Object.fromEntries(rules.flatMap((r) => r.headers).map((h) => [h.key, h.value]));
}

test("production sends HSTS together with nosniff, referrer and permissions policies", async () => {
  vi.stubEnv("NODE_ENV", "production");
  const h = await configured();
  expect(h["Strict-Transport-Security"]).toBe("max-age=31536000; includeSubDomains");
  expect(h["X-Content-Type-Options"]).toBe("nosniff");
  expect(h["Referrer-Policy"]).toBe("no-referrer");
  expect(h["Permissions-Policy"]).toBe("camera=(), microphone=(), geolocation=()");
  expect(h["X-Frame-Options"]).toBe("DENY");
});

test("development does not pin the browser to HTTPS", async () => {
  vi.stubEnv("NODE_ENV", "development");
  expect((await configured())["Strict-Transport-Security"]).toBeUndefined();
});

test("production source maps are explicitly off", () => {
  expect(config.productionBrowserSourceMaps).toBe(false);
});

test("the CSP has a fresh nonce per request and stays strict", () => {
  vi.stubEnv("NODE_ENV", "production");
  const one = proxy(new NextRequest("https://app.example/login")).headers.get("Content-Security-Policy")!;
  const two = proxy(new NextRequest("https://app.example/login")).headers.get("Content-Security-Policy")!;
  const nonce = (csp: string) => /'nonce-([^']+)'/.exec(csp)![1];
  expect(nonce(one)).not.toBe(nonce(two));
  const fixed = one.replaceAll(nonce(one)!, "N");
  expect(fixed).toBe([
    "default-src 'self'",
    "script-src 'self' 'nonce-N' 'strict-dynamic'",
    "style-src 'self' 'nonce-N'",
    "img-src 'self' blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; "));
});

test("operator pages are not special-cased by the proxy: it always passes the request on", () => {
  vi.stubEnv("NODE_ENV", "production");
  for (const path of ["/operator/audit", "/operator/review", "/operator/runs"]) {
    expect(proxy(new NextRequest(`https://app.example${path}`)).status).toBe(200);
  }
});

test("the proxy passes the page asked for on to the server, so sign-in can come back to it", () => {
  const forwarded = (url: string, init?: ConstructorParameters<typeof NextRequest>[1]) =>
    proxy(new NextRequest(url, init)).headers.get("x-middleware-request-x-return-to");
  expect(forwarded("https://app.example/accounts/acme.example/people?filter=all")).toBe("/accounts/acme.example/people?filter=all");
  // a value the browser sends itself is replaced, never trusted
  expect(forwarded("https://app.example/accounts", { headers: { "x-return-to": "https://evil.example" } })).toBe("/accounts");
});
