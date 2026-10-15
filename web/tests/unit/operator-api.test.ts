import { afterEach, describe, expect, it, vi } from "vitest";
import { operatorRequest } from "@/lib/operator-api";

describe("operator API contract client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the operator route and validates the response with the generated schema", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ rows: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    const result = await operatorRequest("GET /operator/v1/ops/stuck");

    expect(result).toEqual({ rows: [] });
    expect(fetch).toHaveBeenCalledWith("/api/operator/v1/ops/stuck", expect.objectContaining({
      credentials: "same-origin",
      cache: "no-store",
    }));
  });

  it("rejects a malformed successful response rather than passing it to a screen", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ rows: [{}] }), { status: 200 })));

    await expect(operatorRequest("GET /operator/v1/ops/stuck")).rejects.toThrow("contract");
  });

  it("encodes path parameters and keeps requests on the same origin", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: "a/b", trace_id: "t", status: "unknown", created_at: "x", finished_at: null, makespan_s: null, critical_path_s: null, idle_s: null, missing_sources: [], units: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await operatorRequest("GET /operator/v1/runs/{run_id}/timeline", { params: { run_id: "a/b" } });

    expect(fetch).toHaveBeenCalledWith("/api/operator/v1/runs/a%2Fb/timeline", expect.any(Object));
  });

  it("sends an empty JSON body on a write with no fields (New invite link was refused as 415)", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ tenant_id: "t1", email: "a@b.co", invite_url: "https://x/invite#t", expires_at: "2026-10-22T00:00:00Z" }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await operatorRequest("POST /operator/v1/console/clients/{tenant_id}/invite", { params: { tenant_id: "t1" } }).catch(() => undefined);

    const init = fetch.mock.calls[0]![1] as RequestInit & { headers: Record<string, string> };
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe("{}");
  });
});
