import { start, hscroll, summarize, BASE, OUT } from "./lib.mjs";
const { browser, ctx, page, log } = await start();
const d = process.argv[2] || "orionsystems.example";
const ok = (name, pass, ev = "") => console.log((pass ? "PASS " : "FAIL ") + name + (ev ? " :: " + ev : ""));
for (const [sheet, tabs] of [["people", ["", "summary"]], ["technology", ["", "platform-ranking", "confirmed-stack", "platform-teams"]]]) {
  for (const t of tabs) {
    const r = await page.goto(`${BASE}/accounts/${d}/${sheet}${t ? "?tab=" + t : ""}`, { waitUntil: "networkidle" });
    const txt = (await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 260);
    ok(`${sheet} tab=${t || "main"}`, r.status() === 200 && !/could not be loaded|Table not found/.test(txt), txt);
  }
}
const tabs = await page.$$eval("main a[href*='tab=']", (a) => a.map((x) => x.textContent.trim()));
console.log("technology tabs listed:", JSON.stringify(tabs));
// archive index
await page.goto(`${BASE}/accounts/${d}/archive`, { waitUntil: "networkidle" });
const chips = await page.$$eval("main a", (a) => a.filter((x) => /archive\?kind=|archive$/.test(x.getAttribute("href") || "")).map((x) => x.textContent.trim() + "|" + x.getAttribute("href")));
console.log("archive chips:", JSON.stringify(chips));
for (const kind of ["posting", "page", "profile", "feed_item"]) {
  const api = await ctx.request.get(`${BASE}/v1/accounts/${d}/archive/list?kind=${kind}&limit=50`);
  const j = await api.json();
  console.log(`archive list kind=${kind}: ${api.status()} total=${j.total} items=${j.items?.length}`, JSON.stringify((j.items || []).slice(0, 2)).slice(0, 300));
}
const names = ["Job postings", "Pages and news", "Profiles", "All"];
for (const n of names) {
  const c = page.locator("main").getByRole("link", { name: n, exact: true }).first();
  if (await c.count()) { await c.click(); await page.waitForLoadState("networkidle"); await page.waitForTimeout(400); const cap = await page.locator("main").getByText(/Archived items/).first().innerText().catch(() => "?"); const href = page.url(); console.log(`chip ${n}: ${href} :: ${cap}`); }
}
await page.goto(`${BASE}/accounts/${d}/archive?kind=posting`, { waitUntil: "networkidle" });
console.log("posting kind page:", (await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 500));
// the index links into the viewer
await page.goto(`${BASE}/accounts/${d}/archive`, { waitUntil: "networkidle" });
const hrefs = await page.$$eval("main table a", (a) => a.map((x) => x.textContent.trim() + "|" + x.getAttribute("href")));
console.log("index links sample:", JSON.stringify(hrefs.slice(0, 6)));
const view = page.locator("main table a", { hasText: "View" }).first();
await view.click(); await page.waitForURL(/archive\?url=/); await page.waitForLoadState("networkidle");
ok("index View opens the viewer", /archive\?url=/.test(page.url()), page.url());
// Download JSON
const dj = page.getByRole("link", { name: "Download JSON" });
const href = await dj.getAttribute("href");
const r = await ctx.request.get(BASE + href);
const body = await r.text();
let parsed = null; try { parsed = JSON.parse(body); } catch {}
ok("Download JSON", r.status() === 200 && !!parsed, `${r.status()} ${r.headers()["content-type"]} ${r.headers()["content-disposition"]} keys=${parsed ? Object.keys(parsed).join(",") : body.slice(0, 100)}`);
// unknown archive url
const r404 = await ctx.request.get(`${BASE}/v1/accounts/${d}/archive?url=${encodeURIComponent("https://not-archived.example/x")}`);
ok("archive of an unknown url is 404", r404.status() === 404, String(r404.status()));
await page.goto(`${BASE}/accounts/${d}/archive?url=${encodeURIComponent("https://not-archived.example/x")}`, { waitUntil: "networkidle" });
console.log("unknown url page:", (await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 200));
// mobile
await page.setViewportSize({ width: 390, height: 844 });
for (const p of ["/archive", "/archive?url=" + encodeURIComponent(`https://${d}/careers/jobs/s1`), "/jobs", "/people", "/technology"]) {
  await page.goto(`${BASE}/accounts/${d}${p}`, { waitUntil: "networkidle" });
  const hs = await hscroll(page);
  const name = p.replace(/[^a-z]/g, "").slice(0, 20);
  await page.screenshot({ path: `${OUT}/shot-m390-${name}.png`, fullPage: true });
  ok(`390px no horizontal page scroll ${p.slice(0, 30)}`, !hs);
}
console.log(JSON.stringify(summarize(log, "console-site3.json"), null, 1).split("\n").filter((l) => !/inline style/.test(l)).join("\n"));
await browser.close();
