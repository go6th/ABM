import { start, BASE, OUT } from "./lib.mjs";
const { browser, page } = await start();
await page.goto(BASE + "/accounts/zephyrdynamics.example/refresh", { waitUntil: "networkidle" });
console.log((await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 600));
await page.screenshot({ path: `${OUT}/shot-refresh-form.png`, fullPage: true });
await browser.close();
