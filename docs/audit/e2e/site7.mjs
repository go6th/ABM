import { start, hscroll, summarize, BASE, OUT } from "./lib.mjs";
const { browser, ctx, page, log } = await start();
for (const p of ["/research/new", "/research", "/accounts/orionsystems.example/refresh", "/accounts/add", "/accounts"]) {
  await page.goto(BASE + p, { waitUntil: "networkidle" });
  const t = (await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 700);
  const btns = await page.$$eval("main button, main input, main select, main textarea", (e) => e.map((x) => `${x.tagName.toLowerCase()}[${x.getAttribute("type") || ""}|${x.getAttribute("name") || x.getAttribute("aria-label") || x.textContent.trim().slice(0, 30) || x.getAttribute("placeholder") || ""}]`));
  console.log("##", p, "\n", t, "\n controls:", JSON.stringify(btns).slice(0, 600));
  await page.screenshot({ path: `${OUT}/shot-page-${p.replace(/[^a-z]/gi, "_")}.png`, fullPage: true });
}
console.log(JSON.stringify(summarize(log, "console-site7.json"), null, 1).split("\n").filter((l) => !/inline style/.test(l)).join("\n"));
await browser.close();
