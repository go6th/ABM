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
});
