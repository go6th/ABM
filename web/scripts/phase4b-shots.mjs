// Phase 4B screenshots and click-through on the hermetic stack (fake model, synthetic providers).
// Usage: BASE_URL=http://localhost:3011 DEMO_PASSWORD=... SHOTS_DB_CONTAINER=abm-shots-db-1 \
//   node scripts/phase4b-shots.mjs <outdir> <big-file> <small-file>
// Signs in through the API (the password is never typed into a page). Writes <screen>-<width>.png
// and prints one line per step of the click-through, every horizontal scroll, and every console
// or CSP error. SYNTHETIC demo data only: run it against a throwaway seeded copy of the stack.
import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:3011";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const DB = process.env.SHOTS_DB_CONTAINER ?? "abm-shots-db-1";
const [OUT = "./test-results/phase4b", BIG, SMALL] = process.argv.slice(2);
if (!PASSWORD || !BIG || !SMALL) throw new Error("set DEMO_PASSWORD and pass <outdir> <big-file> <small-file>");
mkdirSync(OUT, { recursive: true });

const sql = (q) => execFileSync("docker", ["exec", DB, "psql", "-U", "postgres", "-d", "ai", "-tAc", q]).toString().trim();
const browser = await chromium.launch({ headless: true });
const errors = [];
const log = (s) => console.log(s);

async function signIn(email) {
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 840 } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`${email} pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`${email} console: ${m.text().slice(0, 240)}`); });
  const r = await page.request.post(`${BASE}/v1/auth/login`, { data: { email, password: PASSWORD }, headers: { Origin: BASE } });
  if (r.status() !== 200) throw new Error(`sign-in ${email}: ${r.status()}`);
  return page;
}

async function operator() {
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 840 } });
  const host = new URL(BASE).hostname;
  await ctx.addCookies([{ name: "__Host-6th_operator_session", value: "operator:replica-review", domain: host, path: "/", secure: true, httpOnly: true, sameSite: "Lax" }, { name: "6th_operator_session", value: "operator:replica-review", domain: host, path: "/", httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`operator pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`operator console: ${m.text().slice(0, 240)}`); });
  return page;
}

async function shot(page, name, width, { url, act, full = false } = {}) {
  await page.setViewportSize({ width, height: width < 500 ? 812 : 840 });
  if (url) await page.goto(BASE + url, { waitUntil: "networkidle", timeout: 60000 });
  if (act) await act();
  await page.waitForTimeout(400);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  await page.screenshot({ path: `${OUT}/${name}-${width}.png`, fullPage: full });
  log(`${wide ? "HSCROLL" : "ok     "} ${name} @${width}`);
}

const priya = await signIn("priya@meridian.example");
const limits = async () => (await (await priya.request.get(`${BASE}/v1/me/limits`)).json());

// 1. Ask with the model chip, New research (both tabs)
for (const w of [1360, 375]) {
  await shot(priya, "ask-model-chip", w, {
    url: "/",
    act: async () => { await priya.getByRole("button", { name: /model: .*Change model|^Model: .*Change model/ }).first().click(); },
  });
  await shot(priya, "new-research-company", w, { url: "/research/new" });
  await shot(priya, "new-research-file", w, { url: "/research/new", act: async () => { await priya.getByRole("tab", { name: "Upload a file" }).click(); } });
}

// 2. A large upload: byte progress (throttled so it can be seen), a reload that continues, parts
const cdp = await priya.context().newCDPSession(priya);
await cdp.send("Network.enable");
for (const [w, file] of [[1360, BIG], [375, SMALL]]) {
  await priya.setViewportSize({ width: w, height: w < 500 ? 812 : 840 });
  await priya.goto(BASE + "/research/new", { waitUntil: "networkidle" });
  await priya.getByRole("tab", { name: "Upload a file" }).click();
  await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 5, downloadThroughput: -1, uploadThroughput: 6 * 1024 * 1024 });
  await priya.locator('input[type="file"]').setInputFiles(file);
  await priya.locator("progress.upload-bar").first().waitFor({ timeout: 30000 });
  await priya.waitForTimeout(4000);
  await shot(priya, "file-uploading", w);
  if (w === 1360) {
    await priya.reload({ waitUntil: "networkidle" });
    await priya.getByRole("tab", { name: "Upload a file" }).click();
    await shot(priya, "file-paused-after-reload", w);
    await priya.locator('input[type="file"]').setInputFiles(file); // the same file continues
    await priya.waitForTimeout(1500);
    await shot(priya, "file-resumed", w);
  }
  await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await priya.locator(`progress[aria-label^="Reading"]`).first().waitFor({ timeout: 180000 });
  await priya.waitForTimeout(3000);
  await shot(priya, "file-reading-parts", w);
}
const docs = sql("SELECT name || ' ' || status || ' ' || coalesce(parts_total, 0) FROM documents ORDER BY created_at DESC LIMIT 2");
log(`documents: ${docs.replace(/\n/g, " | ")}`);

// 3. The limit: one research left, used by a confirmed research on the fakes, then refused
const before = await limits();
sql(`INSERT INTO user_limits (tenant_id, owner_user_id, research_limit, updated_by)
     SELECT tenant_id, 'priya-nair', ${before.researches.used + 1}, 'phase4b-shots' FROM accounts WHERE owner_user_id = 'priya-nair' LIMIT 1
     ON CONFLICT (tenant_id, owner_user_id) DO UPDATE SET research_limit = EXCLUDED.research_limit`);
await priya.setViewportSize({ width: 1360, height: 840 });
await priya.goto(BASE + "/research/new", { waitUntil: "networkidle" });
await priya.getByLabel("Company name").first().fill("Probe Health");
await priya.getByLabel("Website").first().fill("probehealth.example");
await priya.getByRole("button", { name: "Start research" }).click();
await priya.getByRole("button", { name: "Confirm research" }).click();
await priya.waitForURL(/\/accounts$/, { timeout: 30000 });
log(`research started on the fakes; limits now ${JSON.stringify((await limits()).researches)}`);
for (const w of [1360, 375]) await shot(priya, "limit-reached", w, { url: "/research/new" });

// 4. The request flow: Request more from admin, a note, Request sent
await priya.setViewportSize({ width: 1360, height: 840 });
await priya.goto(BASE + "/research/new", { waitUntil: "networkidle" });
await priya.getByRole("button", { name: "Request more from admin" }).click();
await priya.getByLabel("Note for your admin (optional)").fill("Two renewals this month need a research each.");
await shot(priya, "request-note", 1360);
await priya.getByRole("button", { name: "Send request" }).click();
await priya.getByText("Request sent.").waitFor();
await shot(priya, "request-sent", 1360);
await shot(priya, "request-sent", 375, { url: "/research/new" });

// 5. The admin's Requests list, then Approve
const op = await operator();
for (const w of [1360, 375]) await shot(op, "admin-requests", w, { url: "/operator/users" });
await op.setViewportSize({ width: 1360, height: 840 });
await op.getByRole("button", { name: "Approve" }).first().click();
await op.waitForTimeout(1500);
await shot(op, "admin-request-approved", 1360);
log(`after approval: ${JSON.stringify((await limits()).researches)}`);

// 6. Download everything: built in the background, parts with short-lived links, a byte range
await priya.setViewportSize({ width: 1360, height: 840 });
await priya.goto(BASE + "/accounts/highmark.example", { waitUntil: "networkidle" });
await priya.getByRole("button", { name: "Download everything" }).click();
await priya.getByText(/^Ready: /).waitFor({ timeout: 180000 });
const link = await priya.locator('ul[aria-label="Download parts"] a').first().getAttribute("href");
const whole = await priya.request.get(BASE + link);
const range = await priya.request.get(BASE + link, { headers: { Range: "bytes=0-99" } });
log(`download part: ${whole.status()} ${whole.headers()["content-type"]} ${(await whole.body()).length} bytes; range ${range.status()} ${range.headers()["content-range"]}`);
for (const w of [1360, 375]) {
  await shot(priya, "download-everything", w, { act: async () => { await priya.getByText(/^Ready: /).scrollIntoViewIfNeeded(); } });
}

// 7. A second user's workspace is empty: first steps, none of Priya's accounts
const dan = await signIn("dan@meridian.example");
for (const w of [1360, 375]) await shot(dan, "second-user-empty", w, { url: "/accounts" });
const theirs = await (await dan.request.get(`${BASE}/v1/accounts/highmark.example/workspace`)).status();
log(`dan opening Priya's account: ${theirs}`);

console.log(errors.length ? `ERRORS (${errors.length}):\n` + [...new Set(errors)].join("\n") : "no console errors");
await browser.close();
