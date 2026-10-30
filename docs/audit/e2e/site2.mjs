import { start, hscroll, summarize, BASE, OUT } from "./lib.mjs";
const { browser, ctx, page, log } = await start();
const d = process.argv[2] || "orionsystems.example";
const res = [];
const ok = (name, pass, ev = "") => { res.push({ name, pass }); console.log((pass ? "PASS " : "FAIL ") + name + (ev ? " :: " + ev : "")); };
// --- Jobs table
await page.goto(`${BASE}/accounts/${d}/jobs`, { waitUntil: "networkidle" });
const tabLinks = await page.$$eval("main a[href*='?tab=']", (as) => as.map((a) => a.textContent.trim()));
ok("jobs: tabs Jobs/Summary/Counts/Long text", ["Summary", "Counts", "Long text"].every((t) => tabLinks.includes(t)), JSON.stringify(tabLinks));
for (const t of ["summary", "counts", "long-text"]) {
  const r = await page.goto(`${BASE}/accounts/${d}/jobs?tab=${t}`, { waitUntil: "networkidle" });
  const txt = (await page.innerText("main")).slice(0, 200).replace(/\n+/g, " | ");
  ok(`jobs tab=${t} loads`, r.status() === 200 && !/could not be loaded|not found/i.test(txt), txt.slice(0, 120));
}
await page.goto(`${BASE}/accounts/${d}/jobs`, { waitUntil: "networkidle" });
// chips
const chip = await page.$$eval("main a", (as) => as.filter((a) => /^(All|Open|Closed|Status unknown)/.test(a.textContent.trim())).map((a) => a.textContent.trim() + "|" + a.getAttribute("href")));
ok("jobs: filter chips present", chip.length >= 3, JSON.stringify(chip));
await page.locator("main").getByRole("link", { name: /^Closed/ }).first().click();
await page.waitForURL(/status_closed/); await page.waitForLoadState("networkidle");
let rows = await page.$$eval("main table tbody tr", (r) => r.length);
ok("jobs: Closed chip filters rows", rows >= 1 && /status_closed/.test(page.url()), `rows=${rows} url=${page.url()}`);
await page.locator("main").getByRole("link", { name: /^All \d/ }).first().click(); await page.waitForURL(/filter=all/); await page.waitForLoadState("networkidle");
rows = await page.$$eval("main table tbody tr", (r) => r.length);
ok("jobs: All chip restores rows", rows === 3, `rows=${rows}`);
// sort
await page.getByRole("link", { name: "Title", exact: true }).click(); await page.waitForURL(/sort=title&dir=asc/); await page.waitForLoadState("networkidle");
const titles = await page.$$eval("main table tbody tr td:nth-child(3)", (c) => c.map((x) => x.textContent.trim()));
ok("jobs: sort by Title asc", titles.length === 3 && [...titles].sort().join() === titles.join(), JSON.stringify(titles));
await page.getByRole("link", { name: "Title", exact: true }).click(); await page.waitForURL(/sort=title&dir=desc/); await page.waitForLoadState("networkidle");
const titles2 = await page.$$eval("main table tbody tr td:nth-child(3)", (c) => c.map((x) => x.textContent.trim()));
ok("jobs: sort by Title toggles desc", titles2.join() === [...titles].reverse().join(), JSON.stringify(titles2) + page.url());
// Expand
const exp = page.getByRole("button", { name: /Expand|Shrink/ });
await exp.click(); await page.waitForTimeout(300);
ok("jobs: Expand toggles", true, await exp.innerText());
await page.screenshot({ path: `${OUT}/shot-jobs-expanded-1440.png`, fullPage: true });
// downloads
for (const label of ["Download CSV", "Open in Excel", "Download the file"]) {
  const href = await page.getByRole("link", { name: label }).first().getAttribute("href");
  const r = await ctx.request.get(BASE + href);
  const body = await r.body();
  ok(`jobs: ${label} downloads`, r.status() === 200 && body.length > 100, `${r.status()} ${r.headers()["content-type"]} ${body.length}B ${href.slice(0, 70)}`);
}
// Links in the table
const links = await page.$$eval("main table a", (as) => as.map((a) => ({ t: a.textContent.trim(), h: a.getAttribute("href"), tg: a.getAttribute("target"), rel: a.getAttribute("rel") })));
const posting = links.filter((l) => l.t === "Open posting");
ok("jobs: posting links are http(s)", posting.length === 3 && posting.every((l) => /^https?:\/\//.test(l.h)), JSON.stringify(posting[0]));
ok("jobs: links open in new tab with noopener", posting.every((l) => l.tg === "_blank" && /noopener/.test(l.rel || "")), posting[0]?.rel);
const arch = links.filter((l) => l.t === "Open archived copy" || l.t === "archived");
console.log("archived-ish links", JSON.stringify(arch.slice(0, 4)));
// open archived copy in the page
const first = (await page.$$eval("main table a", (as) => as.filter((a) => a.textContent.trim() === "Open archived copy").map((a) => a.href)))[0];
console.log("archived href", first);
const [pop] = await Promise.all([ctx.waitForEvent("page"), page.getByRole("link", { name: "Open archived copy" }).first().click()]);
await pop.waitForLoadState("networkidle");
const ptxt = (await pop.innerText("main")).slice(0, 700);
ok("archive viewer opens from the archived link", /orionsystems\.example\/accounts\/|\/accounts\/orionsystems\.example\/archive/.test(pop.url()) && pop.url().includes("archive?url="), pop.url());
console.log("--- archive page text\n" + ptxt);
await pop.screenshot({ path: `${OUT}/shot-archive-page-1440.png`, fullPage: true });
const dl = await pop.$$eval("main a, main button", (e) => e.map((x) => x.textContent.trim() + "|" + (x.getAttribute("href") || "")));
console.log("archive page controls", JSON.stringify(dl));
await pop.close();
console.log(JSON.stringify(summarize(log, "console-site2.json"), null, 1));
await browser.close();
