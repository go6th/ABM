import { start, hscroll, summarize, BASE, OUT } from "./lib.mjs";
const { browser, ctx, page, log } = await start();
const ok = (name, pass, ev = "") => console.log((pass ? "PASS " : "FAIL ") + name + (ev ? " :: " + ev : ""));
await page.goto(`${BASE}/accounts/orionsystems.example/jobs`, { waitUntil: "networkidle" });
const rows = await page.$$eval("main table tbody tr", (trs) => trs.map((tr) => ({ cells: [...tr.children].map((c) => c.textContent.trim().slice(0, 28)), cls: [...tr.children].map((c) => c.className).filter(Boolean).slice(0, 3) })));
console.log(JSON.stringify(rows.map((r) => [r.cells[1], r.cells[2], r.cells[5], r.cls.join(",")])));
const newCol = await page.$$eval("main table thead th", (ths) => ths.map((t) => t.textContent.trim()).indexOf("New since last research"));
const flagged = await page.$$eval("main table tbody tr", (trs, i) => trs.map((tr) => ({ t: tr.children[3]?.textContent.trim(), v: tr.children[i]?.textContent.trim(), c: tr.children[i]?.className })), newCol);
console.log("new column idx", newCol, JSON.stringify(flagged));
ok("web: exactly one row flagged new (the injected posting)", flagged.filter((f) => f.v === "Yes").length === 1 && /Solution Architect/.test(flagged.find((f) => f.v === "Yes")?.t || ""));
ok("web: flagged cell is highlighted", /fill|new|hl|mark/i.test(flagged.find((f) => f.v === "Yes")?.c || ""), flagged.find((f) => f.v === "Yes")?.c);
await page.screenshot({ path: `${OUT}/shot-orion-jobs-new-flag-1440.png`, fullPage: true });
// demo account (seeded), the Meridian ones
for (const dom of ["highmark.example", "cigna.example"]) {
  for (const s of ["people", "jobs", "technology", "archive"]) {
    const r = await page.goto(`${BASE}/accounts/${dom}/${s}`, { waitUntil: "networkidle" });
    const t = (await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 150);
    console.log(dom, s, r.status(), t);
  }
}
console.log(JSON.stringify(summarize(log, "console-site5.json"), null, 1).split("\n").filter((l) => !/inline style/.test(l)).join("\n"));
await browser.close();
