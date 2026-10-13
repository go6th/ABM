// Screenshots of every screen at desktop and phone width, for the visual before/after record.
// Usage: E2E_EMAIL=... E2E_PASSWORD=... node scripts/visual-shots.mjs <outdir> [--reduced]
// Needs the local stack (API on :8080) and the web app on :3000. The operator console is opened
// with the dev staff verifier (APP_ENV=dev only). Reports horizontal page scroll, CSP violations
// and other console errors per screen.
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const OUT = process.argv[2] ?? "./test-results/visual";
const REDUCED = process.argv.includes("--reduced");
const WIDTHS = (process.env.SHOT_WIDTHS ?? "1280,375").split(",").map(Number);
const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;
if (!EMAIL || !PASSWORD) throw new Error("set E2E_EMAIL and E2E_PASSWORD");
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ reducedMotion: REDUCED ? "reduce" : "no-preference" });
const page = await ctx.newPage();
let current = "";
const problems = [];
page.on("pageerror", (e) => problems.push(`${current} pageerror: ${e.message.slice(0, 160)}`));
page.on("console", (msg) => {
  if (msg.type() !== "error") return;
  const text = msg.text();
  const kind = /Content Security Policy|Content-Security-Policy|violates/i.test(text) ? "CSP" : "console";
  problems.push(`${current} ${kind}: ${text.slice(0, 160)}`);
});

async function shot(name, url, width) {
  current = `${name}@${width}`;
  await page.setViewportSize({ width, height: width < 500 ? 812 : 860 });
  await page.goto(BASE + url, { waitUntil: "load", timeout: 60000 });
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => undefined);
  await page.waitForTimeout(800);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  const title = await page.title();
  await page.screenshot({ path: `${OUT}/${name}-${width}.png`, fullPage: true });
  console.log(`${wide ? "HSCROLL" : "ok     "} ${name.padEnd(18)} @${width}  "${title}"`);
}

for (const w of WIDTHS) {
  await shot("login", "/login", w);
  await shot("forgot", "/forgot", w);
  await shot("not-found", "/no-such-page", w);
  await shot("operator-login", "/operator/login", w);
}

await page.setViewportSize({ width: 1280, height: 860 });
await page.goto(BASE + "/login", { waitUntil: "networkidle" });
await page.getByLabel("Work email").fill(EMAIL);
await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
await page.getByRole("button", { name: /sign in/i }).click();
await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 60000 });

const domain = await page.evaluate(async () => {
  const r = await fetch("/v1/accounts?limit=5", { credentials: "same-origin" });
  const j = await r.json();
  return j.accounts?.[0]?.domain ?? null;
});
const runId = process.env.SHOT_RUN_ID ?? null;

for (const w of WIDTHS) {
  await shot("ask", "/", w);
  await shot("accounts", "/accounts", w);
  await shot("research-company", "/research/new", w);
  await shot("research-file", "/research/new?mode=file", w);
  if (runId) await shot("run", `/runs/${runId}`, w);
  if (domain) {
    const d = encodeURIComponent(domain);
    for (const tab of ["", "/people", "/jobs", "/technology", "/news", "/sources", "/files"]) {
      await shot(`account${tab.replace("/", "-")}`, `/accounts/${d}${tab}`, w);
    }
  }
}

const url = new URL(BASE);
await ctx.addCookies([{ name: "6th_operator_session", value: "operator:dev-operator", domain: url.hostname, path: "/" }]);
for (const w of WIDTHS) {
  for (const [name, path] of [["op-overview", "/operator"], ["op-clients", "/operator/clients"], ["op-users", "/operator/users"], ["op-runs", "/operator/runs"], ["op-settings", "/operator/settings"]]) {
    await shot(name, path, w);
  }
}

console.log(problems.length ? `problems (${problems.length}):\n${[...new Set(problems)].slice(0, 40).join("\n")}` : "problems: none");
await browser.close();
