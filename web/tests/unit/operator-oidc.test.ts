import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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

  it("reads the client secret from a file path so the value is never in the environment", () => {
    const dir = mkdtempSync(join(tmpdir(), "operator-oidc-"));
    const file = join(dir, "secret");
    writeFileSync(file, "file-secret\n");
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("OPERATOR_GOOGLE_CLIENT_ID", "client-id");
    vi.stubEnv("OPERATOR_GOOGLE_CLIENT_SECRET", "");
    vi.stubEnv("OPERATOR_GOOGLE_CLIENT_SECRET_FILE", file);
    vi.stubEnv("OPERATOR_GOOGLE_REDIRECT_URI", "http://localhost:3000/api/operator/callback");
    vi.stubEnv("STAFF_WORKSPACE_DOMAIN", "example.com");
    vi.stubEnv("API_ORIGIN", "http://127.0.0.1:8080");

    expect(googleOperatorOidcConfig()?.clientSecret).toBe("file-secret");
    vi.stubEnv("OPERATOR_GOOGLE_CLIENT_SECRET_FILE", join(dir, "missing"));
    expect(googleOperatorOidcConfig()).toBeNull(); // no secret: operator sign-in stays off
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

  it("allows only the exact in-cluster API service over private HTTP", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("API_ORIGIN", "http://api:8080");
    vi.stubEnv("OPERATOR_GOOGLE_CLIENT_ID", "client-id");
    vi.stubEnv("OPERATOR_GOOGLE_CLIENT_SECRET", "server-secret");
    vi.stubEnv("OPERATOR_GOOGLE_REDIRECT_URI", "https://accounts.example.com/api/operator/callback");
    vi.stubEnv("STAFF_WORKSPACE_DOMAIN", "example.com");

    expect(operatorApiOrigin()?.origin).toBe("http://api:8080");
    expect(googleOperatorOidcConfig()?.secureCookies).toBe(true);
    vi.stubEnv("API_ORIGIN", "http://api.attacker.test:8080");
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
