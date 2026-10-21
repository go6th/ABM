// Every built screen of the replica: axe (WCAG 2.1 A and AA), zero console errors including
// Content Security Policy reports, no horizontal scroll, and no "not found". Runs against the
// replica stack with the demo seed; signs in as the demo user and, in the console, as a dev
// operator (the dev staff verifier, APP_ENV=dev only).
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const DEMO = "priya@meridian.example";
const ACCOUNT = "/accounts/highmark.example";

const CLIENT = [
  "/", "/accounts", "/accounts/add", ACCOUNT, `${ACCOUNT}/people`, `${ACCOUNT}/signals`, `${ACCOUNT}/jobs`,
  `${ACCOUNT}/technology`, `${ACCOUNT}/news`, `${ACCOUNT}/mail-and-drive`, `${ACCOUNT}/sources`, `${ACCOUNT}/coverage`,
  `${ACCOUNT}/files`, `${ACCOUNT}/refresh`, "/research", "/research/new", "/schedules", "/mail-and-drive",
];
const OPERATOR = ["/operator", "/operator/clients", "/operator/users", "/operator/library", "/operator/runs", "/operator/defaults", "/operator/view"];
const PUBLIC = ["/login", "/forgot", "/reset", "/verify-email", "/invite", "/operator/login"];

function watch(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error" && !/Download the React DevTools/.test(m.text())) errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

async function check(page: Page, path: string, errors: string[]) {
  const reply = await page.goto(path);
  expect(reply?.status() ?? 0, `${path} status`).toBeLessThan(400);
  await expect(page.locator("h1").first()).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(800); // let client data settle before axe and the console check
  await expect(page.locator("body")).not.toContainText(/not found|page could not be found/i);
  const overflow = await page.evaluate(() => document.scrollingElement!.scrollWidth - window.innerWidth);
  expect(overflow, `${path} scrolls sideways`).toBeLessThanOrEqual(1);
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(axe.violations.map((v) => `${path} ${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
  expect(errors, `${path} console`).toEqual([]);
}

test("public screens", async ({ page }) => {
  const errors = watch(page);
  for (const path of PUBLIC) await check(page, path, errors);
});

test("the client workspace screens", async ({ page, baseURL }) => {
  test.skip(!PASSWORD, "set DEMO_PASSWORD to the demo seed's password");
  const errors = watch(page);
  // Signed in through the API with the browser's cookie jar: the password is never typed into a
  // page, so no snapshot or trace can hold it.
  const login = await page.request.post("/v1/auth/login", { data: { email: DEMO, password: PASSWORD }, headers: { Origin: baseURL! } });
  expect(login.status(), "demo sign-in").toBe(200);
  for (const path of CLIENT) await check(page, path, errors);
});

test("the admin console screens", async ({ page, baseURL }) => {
  const errors = watch(page);
  const host = new URL(baseURL!).hostname;
  await page.context().addCookies([
    { name: "__Host-6th_operator_session", value: "operator:replica-review", domain: host, path: "/", secure: true, httpOnly: true, sameSite: "Lax" },
    { name: "6th_operator_session", value: "operator:replica-review", domain: host, path: "/", httpOnly: true, sameSite: "Lax" },
  ]);
  for (const path of OPERATOR) await check(page, path, errors);
  await page.goto("/operator/clients");
  await page.getByRole("link", { name: /Meridian Health Partners/ }).click();
  await expect(page.getByRole("heading", { name: "Meridian Health Partners" })).toBeVisible();
  await check(page, page.url().replace(baseURL!, ""), errors);
});
