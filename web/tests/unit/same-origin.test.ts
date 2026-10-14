import { describe, expect, it } from "vitest";
import { isSameOriginRequest, isSameOriginRouteRequest, publicOrigin } from "@/lib/same-origin";

const localRequest = {
  origin: "http://localhost:3001",
  host: "localhost:3001",
  forwardedHost: null,
  protocol: "http:",
  forwardedProtocol: null,
};

describe("same-origin API proxy guard", () => {
  it("accepts the public host when the framework URL uses an internal port", () => {
    expect(isSameOriginRequest(localRequest)).toBe(true);
  });

  it("rejects a different host or port", () => {
    expect(isSameOriginRequest({ ...localRequest, origin: "http://attacker.test:3001" })).toBe(false);
    expect(isSameOriginRequest({ ...localRequest, origin: "http://localhost:3000" })).toBe(false);
  });

  it("uses the forwarded protocol for HTTPS reverse proxies", () => {
    expect(isSameOriginRequest({
      ...localRequest,
      origin: "https://app.example.test",
      host: "app.example.test",
      protocol: "http:",
      forwardedProtocol: "https",
    })).toBe(true);
  });

  it("rejects missing and malformed origins", () => {
    expect(isSameOriginRequest({ ...localRequest, origin: null })).toBe(false);
    expect(isSameOriginRequest({ ...localRequest, origin: "null" })).toBe(false);
  });
});

describe("same-origin check for route handlers (the user and operator proxies)", () => {
  const route = (headers: Record<string, string>) => ({
    headers: new Headers(headers),
    nextUrl: { protocol: "http:" },
  });

  it("accepts the browser's origin when the server listens on another port", () => {
    // the container listens on 3000; the browser reached it as localhost:3011
    expect(isSameOriginRouteRequest(route({ origin: "http://localhost:3011", host: "localhost:3011" }))).toBe(true);
  });

  it("accepts the public origin behind an HTTPS load balancer", () => {
    expect(isSameOriginRouteRequest(route({
      origin: "https://app.example.test",
      host: "app.example.test",
      "x-forwarded-proto": "https",
    }))).toBe(true);
  });

  it("refuses another origin and a missing one", () => {
    expect(isSameOriginRouteRequest(route({ origin: "http://attacker.test", host: "localhost:3011" }))).toBe(false);
    expect(isSameOriginRouteRequest(route({ host: "localhost:3011" }))).toBe(false);
  });
});

describe("the public origin of a route handler's request", () => {
  const route = (headers: Record<string, string>) => ({ headers: new Headers(headers), nextUrl: { protocol: "http:" } });

  it("is the address the browser used, not the server's own", () => {
    expect(publicOrigin(route({ host: "localhost:3011" }))).toBe("http://localhost:3011");
    expect(publicOrigin(route({ host: "app.example.test", "x-forwarded-proto": "https" }))).toBe("https://app.example.test");
  });

  it("is null without a usable host or protocol", () => {
    expect(publicOrigin(route({}))).toBeNull();
    expect(publicOrigin(route({ host: "app.example.test", "x-forwarded-proto": "ftp" }))).toBeNull();
  });
});
