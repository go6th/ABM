import { afterEach, describe, expect, it, vi } from "vitest";
import { googleOperatorOidcConfig, operatorApiOrigin, operatorOAuthCookieNames, safeOperatorReturnPath } from "@/lib/operator-oidc";

describe("operator Google Workspace configuration", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("accepts a local callback only on localhost and the registered callback path", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("OPERATOR_GOOGLE_CLIENT_ID", "client-id");
    vi.stubEnv("OPERATOR_GOOGLE_CLIENT_SECRET", "server-secret");
    vi.stubEnv("OPERATOR_GOOGLE_REDIRECT_URI", "http://localhost:3000/api/operator/callback");
    vi.stubEnv("STAFF_WORKSPACE_DOMAIN", "example.com");
    vi.stubEnv("API_ORIGIN", "http://127.0.0.1:8080");

    const config = googleOperatorOidcConfig();
    expect(config?.clientId).toBe("client-id");
    expect(config?.secureCookies).toBe(false);
    expect(googleOperatorOidcConfig()?.clientSecret).toBe("server-secret");
  });

  it("rejects non-HTTPS production endpoints and non-local HTTP API origins", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("OPERATOR_GOOGLE_CLIENT_ID", "client-id");
    vi.stubEnv("OPERATOR_GOOGLE_CLIENT_SECRET", "server-secret");
    vi.stubEnv("OPERATOR_GOOGLE_REDIRECT_URI", "http://localhost:3000/api/operator/callback");
    vi.stubEnv("STAFF_WORKSPACE_DOMAIN", "example.com");
    vi.stubEnv("API_ORIGIN", "http://api.internal:8080");

    expect(googleOperatorOidcConfig()).toBeNull();
    expect(operatorApiOrigin()).toBeNull();
  });

  it("uses host cookies for secure origins and refuses off-site return paths", () => {
    expect(operatorOAuthCookieNames(true).session).toBe("__Host-6th_operator_session");
    expect(operatorOAuthCookieNames(false).session).toBe("6th_operator_session");
    expect(safeOperatorReturnPath("/operator/tenants?tab=all")).toBe("/operator/tenants?tab=all");
    expect(safeOperatorReturnPath("//attacker.example/login")).toBe("/operator");
    expect(safeOperatorReturnPath("https://attacker.example/login")).toBe("/operator");
  });
});
