import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://localhost:3000";
const OUT = process.argv[2] ?? "./shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text().slice(0, 200)}`); });

async function shot(name, url, width = 1440) {
  await page.setViewportSize({ width, height: width < 500 ? 844 : 900 });
  await page.goto(BASE + url, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(400);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  await page.screenshot({ path: `${OUT}/${name}-${width}.png`, fullPage: true });
  console.log(`${wide ? "HSCROLL " : "ok      "} ${name} @${width} -> ${page.url()}`);
}

await shot("login", "/login");
await page.setViewportSize({ width: 1440, height: 900 });
await page.goto(BASE + "/login", { waitUntil: "networkidle" });
const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;
if (!EMAIL || !PASSWORD) throw new Error("set E2E_EMAIL and E2E_PASSWORD");
await page.getByLabel("Work email").fill(EMAIL);
await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
await page.getByRole("button", { name: "Sign in to workspace" }).click();
await page.waitForURL(BASE + "/", { timeout: 60000 });
console.log("signed in");

const domain = await page.evaluate(async () => {
  const r = await fetch("/v1/accounts?limit=5", { credentials: "same-origin" });
  const j = await r.json();
  return j.accounts?.[0]?.domain ?? null;
});
console.log("first account:", domain);

for (const w of [1440, 390]) {
  await shot("ask", "/", w);
  await shot("accounts", "/accounts", w);
  await shot("research-new", "/research/new", w);
  await shot("research-file", "/research/new?mode=file", w);
  if (domain) {
    const d = encodeURIComponent(domain);
    await shot("account", `/accounts/${d}`, w);
    await shot("account-people", `/accounts/${d}/people`, w);
    await shot("account-jobs", `/accounts/${d}/jobs`, w);
    await shot("account-news", `/accounts/${d}/news`, w);
    await shot("account-sources", `/accounts/${d}/sources`, w);
    await shot("account-files", `/accounts/${d}/files`, w);
  }
}
await shot("operator-login", "/operator/login");
await shot("operator-login", "/operator/login", 390);
console.log("errors:", errors.length ? errors.slice(0, 15).join("\n") : "none");
await browser.close();
