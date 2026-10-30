import { createRequire } from "node:module";
const require = createRequire("/home/sakib/ABM Generator/web/package.json");
export const { chromium } = require("@playwright/test");
export const BASE = "http://localhost:3009";
export const OUT = "/home/sakib/ABM Generator/docs/audit/e2e";

export async function start(email = "priya@meridian.example", viewport = { width: 1440, height: 900 }) {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const log = { console: [], pageerrors: [], failed: [], bad: [] };
  page.on("pageerror", (e) => log.pageerrors.push(`${page.url()} :: ${e.message}`));
  page.on("console", (m) => { if (["error", "warning"].includes(m.type())) log.console.push(`${m.type()} ${page.url()} :: ${m.text().slice(0, 300)}`); });
  page.on("requestfailed", (r) => log.failed.push(`${r.method()} ${r.url()} :: ${r.failure()?.errorText}`));
  page.on("response", (r) => { if (r.status() >= 400) log.bad.push(`${r.status()} ${r.request().method()} ${r.url()}`); });
  await page.goto(BASE + "/login", { waitUntil: "networkidle" });
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill("E2eDemo-2026!x");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 60000 });
  return { browser, ctx, page, log };
}
export async function hscroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
}
import { writeFileSync } from "node:fs";
export function summarize(log, file) {
  writeFileSync(`${OUT}/${file}`, JSON.stringify(log, null, 1));
  const norm = (s) => s.replace(/'sha256-[^']+'/g, "'sha256-…'").replace(/nonce-[A-Za-z0-9=]+/g, "nonce-…");
  const count = (arr) => { const m = new Map(); for (const a of arr) m.set(norm(a), (m.get(norm(a)) || 0) + 1); return [...m].map(([k, v]) => `${v}x ${k.slice(0, 260)}`); };
  return { console: count(log.console), pageerrors: count(log.pageerrors), failed: count(log.failed), bad: count(log.bad) };
}
