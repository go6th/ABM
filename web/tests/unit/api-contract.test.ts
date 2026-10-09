import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/lib/api";

describe("tenant API contract client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("matches a generated route when the request includes query parameters", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ accounts: [], next_after: null }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetch);

    const result = await api<{ accounts: unknown[]; next_after: string | null }>(
      "GET",
      "/v1/accounts?limit=200",
    );

    expect(result).toEqual({ accounts: [], next_after: null });
    expect(fetch).toHaveBeenCalledWith("/v1/accounts?limit=200", expect.objectContaining({
      credentials: "same-origin",
      cache: "no-store",
    }));
  });

  it("rejects a successful response that does not match the generated schema", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ accounts: [{}], next_after: null }), { status: 200 }),
    ));

    await expect(api("GET", "/v1/accounts")).rejects.toMatchObject({
      status: 502,
      type: "contract_mismatch",
    });
  });

  it("rejects an invalid request before making a network call", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    await expect(api("POST", "/v1/accounts", { domain: 42 })).rejects.toMatchObject({
      status: 0,
      type: "request_contract_mismatch",
    } satisfies Partial<ApiError>);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("refuses a path that is not in the generated API contract", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    await expect(api("GET", "/v1/not-a-route")).rejects.toMatchObject({
      type: "operation_not_in_contract",
    });
    expect(fetch).not.toHaveBeenCalled();
  });
});
