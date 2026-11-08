// Captures every client screen of the running app (http://localhost:3000) as JPEG for the report:
// desktop 1440 and phone 390, plus the Refresh pop-up armed (pressed once only, so nothing starts;
// every start/refresh POST is also blocked here as a guard) and the open menus.
// Usage: E2E_EMAIL=... E2E_PASSWORD=... node shots.mjs <outdir>
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire("/home/sakib/ABM Generator/web/package.json");
const { chromium } = require("@playwright/test");

const BASE = process.env.BASE ?? "http://localhost:3000";
const OUT = process.argv[2];
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
await page.route(/\/v1\/(research|runs|accounts\/[^/]+\/refresh)(\/resume)?$/, (r) => (r.request().method() === "POST" ? r.abort() : r.continue()));
const problems = [];
page.on("pageerror", (e) => problems.push(`pageerror ${e.message}`));
page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("Content Security Policy")) problems.push(m.text().slice(0, 200)); });

const snap = async (name, opts = {}) => page.screenshot({ path: `${OUT}/${name}.jpg`, type: "jpeg", quality: 82, ...opts });
async function shot(name, url, width = 1440) {
  await page.setViewportSize({ width, height: width < 500 ? 844 : 900 });
  await page.goto(BASE + url, { waitUntil: "networkidle", timeout: 90000 });
  await page.waitForTimeout(700);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  if (wide) problems.push(`sideways scroll: ${name} @${width}`);
  await snap(`${name}-${width}`, { fullPage: width < 500 });
}

await shot("signin", "/login");
await page.getByLabel("Work email").fill(process.env.E2E_EMAIL);
await page.getByLabel("Password", { exact: true }).fill(process.env.E2E_PASSWORD);
await page.getByRole("button", { name: "Sign in", exact: true }).click();
await page.waitForURL(BASE + "/", { timeout: 60000 });
const d = encodeURIComponent(process.env.DOMAIN ?? "holdings-demo.example");
const screens = [
  ["ask", "/"], ["chats", "/chats"], ["accounts", "/accounts"], ["research", "/research"],
  ["account", `/accounts/${d}`], ["people", `/accounts/${d}/people`], ["jobs", `/accounts/${d}/jobs`],
  ["technology", `/accounts/${d}/technology`], ["sources", `/accounts/${d}/sources`], ["files", `/accounts/${d}/files`],
  ["settings", "/settings"], ["auto-refresh", "/settings/auto-refresh"], ["team", "/settings/team"],
  ["mail-drive", "/settings/mail-and-drive"], ["support", "/settings/support-access"],
];
for (const [n, u] of screens) await shot(n, u);
for (const [n, u] of [["ask", "/"], ["accounts", "/accounts"], ["account", `/accounts/${d}`], ["research", "/research"]]) await shot(n, u, 390);

await page.setViewportSize({ width: 1440, height: 900 });
await page.goto(`${BASE}/accounts/${d}`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: "Refresh", exact: true }).first().click();
const dialog = page.getByRole("dialog");
await dialog.getByRole("button", { name: "Refresh", exact: true }).waitFor({ timeout: 20000 });
await page.waitForTimeout(400);
await snap("popup-refresh");
await dialog.getByRole("button", { name: "Refresh", exact: true }).click(); // first press: arms only
await page.waitForTimeout(400);
await snap("popup-refresh-armed");
await page.keyboard.press("Escape");
await page.locator("summary.ptab-more").click();
await page.waitForTimeout(300);
await snap("menu-more", { clip: { x: 240, y: 0, width: 1200, height: 560 } });
await page.keyboard.press("Escape");
await page.locator('summary[aria-label="More actions"]').click();
await page.getByRole("button", { name: "Delete account" }).click();
await page.waitForTimeout(400);
await snap("popup-delete");
await page.keyboard.press("Escape");
await page.getByRole("link", { name: "Jobs", exact: true }).click();
await page.waitForTimeout(70);
await snap("feedback-tab-click", { clip: { x: 0, y: 0, width: 1440, height: 240 } });
await page.waitForLoadState("networkidle");
console.log(problems.length ? problems.join("\n") : "no console errors, no sideways scroll");
await browser.close();
