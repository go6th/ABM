import { chromium } from "@playwright/test";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const file = pathToFileURL(resolve("shots/structured-preview.html")).href;
const browser = await chromium.launch({ headless: true });
for (const [w, h] of [[1440, 900], [390, 844]]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  await page.goto(file, { waitUntil: "networkidle" });
  await page.waitForTimeout(300);
  const wide = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  await page.screenshot({ path: `shots/structured-answer-${w}.png`, fullPage: true });
  console.log(`${wide ? "HSCROLL " : "ok      "} structured-answer @${w}`);
  await page.close();
}
await browser.close();
