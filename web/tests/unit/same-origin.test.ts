import { describe, expect, it } from "vitest";
import { isSameOriginRequest } from "@/lib/same-origin";

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