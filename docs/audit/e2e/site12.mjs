import { start, BASE, OUT } from "./lib.mjs";
const { browser, page } = await start();
await page.goto(BASE + "/accounts/stopcheck.example", { waitUntil: "networkidle" });
const t = (await page.innerText("main")).replace(/\n+/g, " | ");
console.log(t.slice(0, 500));
const r = page.getByRole("button", { name: /Resume research/ }).or(page.getByRole("link", { name: /Resume research/ }));
console.log("resume controls on account page:", await r.count());
await page.screenshot({ path: `${OUT}/shot-account-stopped-resume.png`, fullPage: true });
if (await r.count()) { await r.first().click(); await page.waitForTimeout(3000); console.log("after resume click:", page.url(), (await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 300)); }
await browser.close();
