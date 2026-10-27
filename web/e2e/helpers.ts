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
  // The sign-in page finds a company's SSO from the work email's domain, which the seeded
  // tenants share; the harness opens the tenant's own sign-in start through the web proxy
  // (same cookie jar), which the route above answers with the fake IdP's redirect.
  await page.goto(`/v1/sso/${encodeURIComponent(slug)}/start?return_to=%2F`);
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
}

export async function noSeriousA11y(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
}
