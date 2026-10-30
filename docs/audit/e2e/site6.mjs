import { start, BASE, OUT } from "./lib.mjs";
const { browser, page } = await start();
await page.goto(`${BASE}/accounts/orionsystems.example/jobs`, { waitUntil: "networkidle" });
const r = await page.$$eval("main table tbody tr", (trs) => trs.map((tr) => [...tr.children].map((c) => getComputedStyle(c).backgroundColor + "|" + c.className).filter((x, i) => i < 6 || i > 14)));
console.log(JSON.stringify(r[0]), "\n", JSON.stringify(r[1]));
await browser.close();
