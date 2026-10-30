import { start, BASE, OUT } from "./lib.mjs";
const { browser, page } = await start();
await page.goto(BASE + "/accounts/zephyrdynamics.example/refresh", { waitUntil: "networkidle" });
await page.locator("main button", { hasText: "Full refresh" }).first().click();
await page.locator("main button", { hasText: "Confirm refresh" }).click(); await page.getByRole("button", { name: "Proceed" }).click();
await page.waitForTimeout(3500);
console.log(page.url(), (await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 400));
console.log("buttons:", JSON.stringify(await page.$$eval("main button", (b) => b.map((x) => x.textContent.trim()))));
await browser.close();
