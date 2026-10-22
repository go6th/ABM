import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, apiPublic, clearSession, CSRF_REFUSED } from "@/lib/api";

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

describe("CSRF recovery", () => {
  beforeEach(() => clearSession());
  afterEach(() => { clearSession(); vi.unstubAllGlobals(); });

  const sessionReply = (token: string) => new Response(JSON.stringify({ user_id: "u", role: "analyst", credential: "c", step_up_fresh: false, csrf_token: token }), { status: 200 });
  const refused = () => new Response(JSON.stringify({ type: "urn:6th:problem:csrf_refused", title: "Request refused", status: 403, detail: "stale" }),
    { status: 403, headers: { "content-type": "application/problem+json" } });
  const added = () => new Response(JSON.stringify({ account_id: "a", created: true, domain: "acme.example" }), { status: 201 });

  it("reads the session again and replays once when the API refuses the token", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(sessionReply("t1"))
      .mockResolvedValueOnce(refused())
      .mockResolvedValueOnce(sessionReply("t2"))
      .mockResolvedValueOnce(added());
    vi.stubGlobal("fetch", fetch);
    const result = await api("POST", "/v1/accounts", { domain: "acme.example" });
    expect(result).toEqual({ account_id: "a", created: true, domain: "acme.example" });
    const posts = fetch.mock.calls.filter((c) => (c[1] as RequestInit).method === "POST");
    expect(posts.map((c) => ((c[1] as RequestInit).headers as Record<string, string>)["X-CSRF-Token"])).toEqual(["t1", "t2"]);
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it("replays only once: a second refusal is the caller's error", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(sessionReply("t1"))
      .mockResolvedValueOnce(refused())
      .mockResolvedValueOnce(sessionReply("t2"))
      .mockResolvedValueOnce(refused());
    vi.stubGlobal("fetch", fetch);
    await expect(api("POST", "/v1/accounts", { domain: "acme.example" })).rejects.toMatchObject({ status: 403, type: CSRF_REFUSED });
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it("does not replay a 403 of another kind", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(sessionReply("t1"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ type: "urn:6th:problem:step_up_required", detail: "x" }), { status: 403 }));
    vi.stubGlobal("fetch", fetch);
    await expect(api("POST", "/v1/accounts", { domain: "acme.example" })).rejects.toMatchObject({ status: 403, type: "step_up_required" });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe("apiPublic", () => {
  afterEach(() => { clearSession(); vi.unstubAllGlobals(); });

  it("sends no CSRF token and reads no session, and still parses the reply against the contract", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ accepted: true }), { status: 202 }));
    vi.stubGlobal("fetch", fetch);
    const result = await apiPublic("POST", "/v1/auth/password/reset/request", { email: "ann@acme.example" });
    expect(result).toEqual({ accepted: true });
    expect(fetch).toHaveBeenCalledTimes(1);
    const init = fetch.mock.calls[0]![1] as RequestInit;
    expect(init.headers).not.toHaveProperty("X-CSRF-Token");
    expect(init.body).toBe(JSON.stringify({ email: "ann@acme.example" }));
  });

  it("refuses a request or a reply that does not match the contract, before or without trusting it", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ accepted: "yes" }), { status: 202 }));
    vi.stubGlobal("fetch", fetch);
    await expect(apiPublic("POST", "/v1/auth/password/reset/request", { email: 42 })).rejects.toMatchObject({ type: "request_contract_mismatch" });
    expect(fetch).not.toHaveBeenCalled();
    await expect(apiPublic("POST", "/v1/auth/password/reset/request", { email: "a@b.c" })).rejects.toMatchObject({ status: 502, type: "contract_mismatch" });
  });

  it("keeps the whole error body for a reply that is not problem+json (the workspace choice)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: "tenant_selection_required", tenants: [{ tenant_id: "t1", role: "analyst" }] }), { status: 409 }),
    ));
    const error = await apiPublic("POST", "/v1/auth/login", { email: "a@b.c", password: "p" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).body).toEqual({ code: "tenant_selection_required", tenants: [{ tenant_id: "t1", role: "analyst" }] });
  });
});
