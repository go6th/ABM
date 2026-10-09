// Shared E2E helpers: SSO sign-in through the harness's fake IdP, and the axe gate.
import { expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

export const API = process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:8088";
const GROUP: Record<string, string> = { admin: "admins", analyst: "analysts", seller: "sales", viewer: "readers" };

export async function signIn(page: Page, slug: string, subject: string, role: string) {
  await page.request.post(`${API}/fake-idp/next`, { data: { subject, groups: [GROUP[role] ?? "none"] } });
  // The IdP lives at https://idp.synthetic.test (the database refuses http IdPs) and does not
  // resolve; Playwright does not route requests reached through a redirect, so the sign-in start is
  // fetched here (same cookie jar as the browser) and the fake IdP's answer handed back.
  await page.route("**/v1/sso/*/start**", async (route) => {
    const start = await page.request.get(route.request().url(), { maxRedirects: 0 });
    expect(start.status(), "SSO start must return the IdP redirect").toBe(302);
    const location = start.headers()["location"];
    expect(location, "SSO start must return an authorization URL").toBeTruthy();
    const idp = new URL(location!, "http://localhost:3008");
    expect(idp.searchParams.get("code_challenge_method")).toBe("S256");
    const answer = await page.request.get(`${API}/fake-idp/authorize${idp.search}`, { maxRedirects: 0 });
    expect(answer.status(), "the synthetic IdP must accept the authorization request").toBe(302);
    await route.fulfill({ status: 302, headers: { location: answer.headers()["location"]! } });
  });
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  // The form's handler is attached after hydration; on a cold server the first click can land
  // before it. Retry the submit (bounded) until the signed-in page shows.
  await expect(async () => {
    if (!page.url().includes("/login")) return;
    await page.getByLabel("Workspace name").fill(slug);
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "Accounts" })).toBeVisible({ timeout: 3_000 });
  }).toPass({ timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "Accounts" })).toBeVisible();
}

export async function noSeriousA11y(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
}
