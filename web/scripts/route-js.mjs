// JavaScript cost per route: the gzipped size of every same-origin script a fresh page load
// fetches, including chunks loaded after hydration (for example a dynamically imported WebGL
// scene). Also counts CSP violations per route. Run against a production build (`next start`).
// Usage: E2E_EMAIL=... E2E_PASSWORD=... node scripts/route-js.mjs [out.json]
import { chromium } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;
if (!EMAIL || !PASSWORD) throw new Error("set E2E_EMAIL and E2E_PASSWORD");

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
const login = await ctx.newPage();
await login.goto(BASE + "/login", { waitUntil: "networkidle" });
await login.getByLabel("Work email").fill(EMAIL);
await login.getByLabel("Password", { exact: true }).fill(PASSWORD);
await login.getByRole("button", { name: /sign in/i }).click();
await login.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 60000 });
const domain = await login.evaluate(async () => {
  const r = await fetch("/v1/accounts?limit=5", { credentials: "same-origin" });
  return (await r.json()).accounts?.[0]?.domain ?? null;
});
await login.close();
const url = new URL(BASE);
await ctx.addCookies([{ name: "6th_operator_session", value: "operator:dev-operator", domain: url.hostname, path: "/" }]);

const d = encodeURIComponent(domain ?? "none");
const routes = [
  ["/login", false], ["/", true], ["/accounts", true], ["/research/new", true],
  [`/accounts/${d}`, true], [`/accounts/${d}/people`, true], ["/operator", true], ["/operator/settings", true],
];

async function measure(path, signedIn) {
  const page = signedIn ? await ctx.newPage() : await (await browser.newContext()).newPage();
  const sizes = new Map();
  let csp = 0;
  page.on("console", (m) => { if (m.type() === "error" && /Content Security Policy/i.test(m.text())) csp += 1; });
  page.on("response", async (res) => {
    if (res.request().resourceType() !== "script" || !res.url().startsWith(BASE)) return;
    try { sizes.set(res.url(), gzipSync(await res.body()).length); } catch { /* aborted */ }
  });
  await page.goto(BASE + path, { waitUntil: "load", timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.close();
  const total = [...sizes.values()].reduce((a, b) => a + b, 0);
  return { path, scripts: sizes.size, gzip_kb: Math.round(total / 102.4) / 10, csp };
}

const out = [];
for (const [path, signedIn] of routes) {
  const r = await measure(path, signedIn);
  out.push(r);
  console.log(`${r.path.padEnd(40)} ${String(r.gzip_kb).padStart(7)} KB gz  ${String(r.scripts).padStart(3)} scripts  csp ${r.csp}`);
}
if (process.argv[2]) writeFileSync(process.argv[2], JSON.stringify(out, null, 2));
await browser.close();
