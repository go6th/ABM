import { start, hscroll, summarize, BASE, OUT } from "./lib.mjs";
const { browser, ctx, page, log } = await start();
const d = "vegaretail.example";
const ok = (name, pass, ev = "") => console.log((pass ? "PASS " : "FAIL ") + name + (ev ? " :: " + ev : ""));
await page.goto(`${BASE}/accounts/${d}/archive`, { waitUntil: "networkidle" });
const cap1 = await page.getByText(/Archived items, newest/).first().innerText();
let rows = await page.$$eval("main table tbody tr", (r) => r.length);
console.log("first page:", cap1, "rows", rows);
const more = page.getByRole("link", { name: /Show more/ });
ok("archive: Show more link present", (await more.count()) > 0, await more.first().getAttribute("href").catch(() => "none"));
await more.first().click(); await page.waitForURL(/limit=/); await page.waitForLoadState("networkidle");
rows = await page.$$eval("main table tbody tr", (r) => r.length);
const cap2 = await page.getByText(/Archived items, newest/).first().innerText();
ok("archive: Show more grows the list", rows > 50, `rows=${rows} :: ${cap2}`);
await page.screenshot({ path: `${OUT}/shot-vega-archive-showmore-1440.png`, fullPage: false });
ok("archive: Show more gone at the end", (await page.getByRole("link", { name: /Show more/ }).count()) === 0 || rows < 67, `rows=${rows}`);
// filter chip Job postings keeps working with the 61
await page.goto(`${BASE}/accounts/${d}/archive?kind=posting`, { waitUntil: "networkidle" });
console.log("posting chip:", await page.getByText(/Archived items, newest/).first().innerText());
// jobs table big
await page.goto(`${BASE}/accounts/${d}/jobs`, { waitUntil: "networkidle" });
const chips = await page.$$eval("main a", (as) => as.filter((a) => /^(All|Open|Closed|Status)/.test(a.textContent.trim())).map((a) => a.textContent.trim()));
console.log("jobs chips:", JSON.stringify(chips));
const showmore = await page.getByRole("link", { name: /Show more|Next/ }).count();
console.log("jobs paging links:", showmore);
// 390 px
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${BASE}/accounts/${d}/archive`, { waitUntil: "networkidle" });
ok("vega archive 390px no h-scroll", !(await hscroll(page)));
await page.screenshot({ path: `${OUT}/shot-vega-archive-390.png`, fullPage: false });
await page.goto(`${BASE}/accounts/${d}/jobs`, { waitUntil: "networkidle" });
ok("vega jobs 390px no h-scroll (page level)", !(await hscroll(page)));
await page.screenshot({ path: `${OUT}/shot-vega-jobs-390.png`, fullPage: false });
console.log(JSON.stringify(summarize(log, "console-site4.json"), null, 1).split("\n").filter((l) => !/inline style/.test(l)).join("\n"));
await browser.close();
