// Screenshots of the replica screens for comparison with the Claude Design prototype.
// Usage: E2E_EMAIL=... E2E_PASSWORD=... node scripts/replica-shots.mjs [outdir] [screen ...]
// Writes <screen>-<width>.png, reports horizontal scroll and every console or CSP error.
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const OUT = process.argv[2] ?? "./test-results/replica";
const ONLY = process.argv.slice(3);
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1360, height: 840 } });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text().slice(0, 240)}`); });

async function shot(name, url, width, { full = false, act } = {}) {
  await page.setViewportSize({ width, height: width < 500 ? 812 : 840 });
  if (url) await page.goto(BASE + url, { waitUntil: "networkidle", timeout: 60000 });
  if (act) await act();
  await page.waitForTimeout(500);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  await page.screenshot({ path: `${OUT}/${name}-${width}.png`, fullPage: full });
  console.log(`${wide ? "HSCROLL" : "ok     "} ${name} @${width}`);
}

const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;
if (!EMAIL || !PASSWORD) throw new Error("set E2E_EMAIL and E2E_PASSWORD");
await page.goto(BASE + "/login", { waitUntil: "networkidle" });
await page.getByLabel("Work email").fill(EMAIL);
await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
await page.getByRole("button", { name: "Sign in to workspace" }).click();
await page.waitForURL(BASE + "/", { timeout: 60000 });

const want = (name) => !ONLY.length || ONLY.includes(name);
async function openRecent(title) {
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  if ((page.viewportSize()?.width ?? 1360) < 760) await page.getByRole("button", { name: "Menu" }).click();
  await page.getByRole("navigation", { name: "Recent chats" }).getByRole("link", { name: title }).click();
  await page.getByText("Answered from your workspace").first().waitFor({ timeout: 30000 });
}
for (const w of [1360, 375]) {
  if (want("search")) {
    await shot("search-chips", "/", w);
    await shot("search-to-topic", "/", w, {
      act: async () => {
        await page.getByRole("button", { name: /Change accounts/ }).first().click();
        await page.getByRole("menuitemradio", { name: "Highmark" }).click();
        await page.getByRole("button", { name: "Search: off" }).first().click();
        await page.getByLabel("Ask a question").fill("Their plans for Salesforce Data Cloud next year");
        await page.getByRole("button", { name: "Search", exact: true }).first().click();
        await page.waitForURL(/\/accounts\/highmark\.example\/refresh\?topic=/);
        await page.waitForLoadState("networkidle");
      },
    });
  }
  if (want("schedules")) {
    await shot("schedules", "/schedules", w);
    if (w > 500) {
      await shot("schedules-from-account", "/accounts/highmark.example", w, {
        act: async () => {
          await page.getByRole("link", { name: "Schedule", exact: true }).click();
          await page.waitForURL(/\/schedules/);
        },
      });
      await shot("schedules-changed", "/schedules", w, {
        act: async () => {
          const group = page.getByRole("group", { name: "Refresh schedule for Availity" });
          await group.getByRole("button", { name: "Monthly" }).click();
          await page.waitForResponse((r) => r.url().includes("/v1/schedules/") && r.request().method() === "PUT");
        },
      });
    }
  }
  if (want("refresh")) {
    await shot("refresh", "/accounts/highmark.example/refresh", w);
    if (w > 500) {
      await shot("refresh-confirmed", "/accounts/cigna.example/refresh", w, {
        act: async () => {
          await page.getByRole("radio", { name: /Job postings only/ }).click();
          await page.getByRole("button", { name: "Confirm refresh" }).click();
          await page.waitForURL(/\/accounts\/cigna\.example$/);
          await page.waitForTimeout(2500);
          await page.reload({ waitUntil: "networkidle" });
        },
      });
    }
  }
  if (want("sheets")) {
    await shot("people", "/accounts/highmark.example/people", w);
    await shot("people-filtered", null, w, {
      act: async () => {
        await page.getByRole("link", { name: /^Priority A · / }).click();
        await page.waitForURL(/filter=priority_a/);
        await page.getByRole("link", { name: /^Role/ }).click();
        await page.waitForURL(/sort=title/);
        if (w > 500) await page.getByRole("button", { name: "Expand" }).click();
      },
    });
    if (w > 500) {
      for (const t of ["signals", "jobs", "technology", "news", "mail-and-drive", "sources"]) {
        await shot(`sheet-${t}`, `/accounts/highmark.example/${t}`, w);
      }
    }
  }
  if (want("accounts")) {
    await shot("accounts", "/accounts", w);
    await shot("workspace-refreshed", "/accounts/highmark.example", w, { full: true });
    await shot("workspace-first-scan", "/accounts/cigna.example", w, { full: true });
  }
  if (want("recent")) {
    await shot("recent-first-call", null, w, { act: async () => { await page.setViewportSize({ width: w, height: w < 500 ? 812 : 840 }); await openRecent("Salesforce roles this month"); } });
    await shot("recent-offer", null, w, { act: async () => { await openRecent("Who owns ServiceNow"); } });
  }
  if (want("ask")) {
    await shot("ask-empty", "/", w);
    await shot("ask-thread", null, w, {
      act: async () => {
        await page.getByRole("button", { name: "Which of my accounts posted Salesforce build roles this month?" }).click();
        await page.getByText("Answered from your workspace").first().waitFor({ timeout: 30000 });
        await page.getByRole("button", { name: /^Source 1/ }).first().click();
      },
    });
    if (w > 500) {
      await shot("ask-followup", null, w, {
        act: async () => {
          await page.getByRole("button", { name: "Who owns ServiceNow at Cigna?" }).first().click();
          await page.getByText("Answered from your workspace").nth(1).waitFor({ timeout: 30000 });
        },
      });
    }
  }
}
await shot("login", "/login", 1360, { act: async () => { await ctx.clearCookies(); await page.reload(); } });
console.log(errors.length ? `ERRORS (${errors.length}):\n` + [...new Set(errors)].join("\n") : "no console errors");
await browser.close();
