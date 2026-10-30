import { start, BASE } from "./lib.mjs";
const { browser, page } = await start();
for (const p of ["/login", "/accounts/orionsystems.example/jobs"]) {
  await page.goto(BASE + p, { waitUntil: "networkidle" });
  const r = await page.evaluate(() => [...document.querySelectorAll("[style]")].slice(0, 12).map((e) => e.tagName + "." + (e.className?.toString().slice(0, 40)) + " style=" + e.getAttribute("style")?.slice(0, 80)));
  const styles = await page.evaluate(() => document.querySelectorAll("style").length);
  console.log(p, "elements with style attr:", r.length, r, "style tags:", styles);
}
await browser.close();
