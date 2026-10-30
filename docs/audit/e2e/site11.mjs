import { start, BASE, OUT } from "./lib.mjs";
const { browser, page, log } = await start();
await page.goto(BASE + "/research", { waitUntil: "networkidle" });
const stop = page.getByRole("button", { name: "Stop", exact: true });
console.log("stop buttons", await stop.count());
if (await stop.count()) {
  await stop.first().click();
  await page.getByRole("button", { name: "Stop now" }).click();
  await page.waitForTimeout(4000);
  console.log((await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 700));
  await page.screenshot({ path: `${OUT}/shot-research-stopped.png`, fullPage: true });
}
await browser.close();
