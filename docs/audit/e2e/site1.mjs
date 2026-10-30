import { start, hscroll, summarize, BASE, OUT } from "./lib.mjs";
const { browser, page, log } = await start();
const domain = process.argv[2] || "orionsystems.example";
const out = {};
for (const [name, path] of [["jobs", `/accounts/${domain}/jobs`], ["people", `/accounts/${domain}/people`], ["technology", `/accounts/${domain}/technology`], ["archive", `/accounts/${domain}/archive`], ["account", `/accounts/${domain}`]]) {
  for (const w of [1440, 390]) {
    await page.setViewportSize({ width: w, height: w < 500 ? 844 : 900 });
    const resp = await page.goto(BASE + path, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(300);
    const hs = await hscroll(page);
    await page.screenshot({ path: `${OUT}/shot-${domain.split(".")[0]}-${name}-${w}.png`, fullPage: true });
    const tabs = await page.$$eval("main a[href*='?tab=']", (as) => as.map((a) => a.textContent.trim() + "|" + a.getAttribute("href")));
    console.log(`${name}@${w} status=${resp.status()} hscroll=${hs} tabs=${JSON.stringify(tabs)}`);
  }
}
console.log(JSON.stringify(summarize(log, "console-site1.json"), null, 1));
await browser.close();
