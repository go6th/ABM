import { start, BASE, OUT } from "./lib.mjs";
const { browser, page } = await start();
for (const t of ["summary", "counts"]) {
  await page.goto(`${BASE}/accounts/orionsystems.example/jobs?tab=${t}`, { waitUntil: "networkidle" });
  const txt = (await page.innerText("main")).replace(/\n+/g, " | ");
  console.log(t, txt.slice(200, 1100), "\n formula leaks:", /=COUNT|=SUM|=IF\(/.test(txt));
}
await browser.close();
