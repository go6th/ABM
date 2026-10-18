// Parity suite helpers. Queries use roles, labels and visible text only, so the suite runs
// unchanged on the old and the rebuilt UI (docs/UI_BACKEND_AUDIT.md, Phase B).
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { API, signIn } from "../helpers";

export { API, signIn };

export interface Seeded { slug_a: string; slug_b: string; a: string; b: string }

export interface TenantResponse {
  status(): number;
  ok(): boolean;
  json(): Promise<unknown>;
  text(): Promise<string>;
}

/** Use the browser's same-origin fetch so secure session cookies reach the /v1 proxy in HTTP tests. */
export async function tenantRequest(
  page: Page,
  method: string,
  path: string,
  options: { data?: unknown; headers?: Record<string, string> } = {},
): Promise<TenantResponse> {
  const result = await page.evaluate(async ({ method, path, hasData, data, headers }) => {
    const response = await fetch(path, {
      method,
      headers: {
        accept: "application/json",
        ...headers,
        ...(hasData ? { "content-type": "application/json" } : {}),
      },
      body: hasData ? JSON.stringify(data) : undefined,
      credentials: "same-origin",
      cache: "no-store",
    });
    return { status: response.status, ok: response.ok, body: await response.text() };
  }, {
    method,
    path,
    hasData: options.data !== undefined,
    data: options.data,
    headers: options.headers ?? {},
  });
  return {
    status: () => result.status,
    ok: () => result.ok,
    json: async () => JSON.parse(result.body) as unknown,
    text: async () => result.body,
  };
}

export async function seeded(request: APIRequestContext): Promise<Seeded> {
  return (await (await request.get(`${API}/fake-idp/tenants`)).json()) as Seeded;
}

/** Headers for a mutation sent with page.request: the /v1 proxy refuses one without a same-origin Origin. */
export async function mutation(page: Page): Promise<Record<string, string>> {
  return { "X-CSRF-Token": await csrf(page) };
}

export async function csrf(page: Page): Promise<string> {
  return ((await tenantRequest(page, "GET", "/v1/sso/session").then((response) => response.json())) as { csrf_token: string }).csrf_token;
}

/** Sign in to the operator console with the dev staff verifier (APP_ENV=test only). */
export async function operatorSignIn(
  page: Page,
  role: "operator" | "support",
  who = "parity",
  origin = process.env.PARITY_WEB_ORIGIN ?? "http://localhost:3001",
) {
  const base = origin;
  await page.context().addCookies([{ name: "6th_operator_session", value: `${role}:${who}`, url: base }]);
}

export async function noSeriousA11y(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
}

/** Collect console errors and failed same-origin requests while a page is used. */
export function watch(page: Page) {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error" && !/Content Security Policy|Download the React DevTools|\[Fast Refresh\]|webpack-hmr|__nextjs/i.test(m.text())) errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}
