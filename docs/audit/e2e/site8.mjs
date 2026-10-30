import { start, summarize, BASE, OUT } from "./lib.mjs";
const { browser, ctx, page, log } = await start();
const ok = (name, pass, ev = "") => console.log((pass ? "PASS " : "FAIL ") + name + (ev ? " :: " + ev : ""));
await page.goto(BASE + "/research/new", { waitUntil: "networkidle" });
await page.getByPlaceholder("Company name").first().fill("Nimbus Labs");
await page.getByPlaceholder("company.com").fill("nimbuslabs.example");
await page.screenshot({ path: `${OUT}/shot-new-research-form.png` });
await page.getByRole("button", { name: "Start research" }).click();
await page.waitForTimeout(2500);
console.log("after Start:", page.url(), (await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 500));
await page.screenshot({ path: `${OUT}/shot-new-research-after-start.png` });
const dialog = page.getByRole("dialog");
if (await dialog.count()) {
  console.log("dialog:", (await dialog.innerText()).replace(/\n+/g, " | ").slice(0, 400));
  const btns = await dialog.getByRole("button").allInnerTexts(); console.log("dialog buttons", btns);
  await dialog.getByRole("button", { name: /Proceed|Start|Confirm/ }).first().click();
  await page.waitForTimeout(2500);
  console.log("after confirm:", page.url());
}
await page.goto(BASE + "/research", { waitUntil: "networkidle" });
await page.waitForTimeout(8000);
const txt = (await page.innerText("main")).replace(/\n+/g, " | ");
console.log(txt.slice(0, 600));
ok("new research appears as running with a Stop button", /Nimbus Labs/.test(txt) && (await page.getByRole("button", { name: "Stop" }).count()) > 0);
// press Stop on Nimbus: select the Nimbus card first
await page.getByRole("button", { name: /Show Nimbus Labs|Showing Nimbus Labs/ }).first().click().catch(() => {});
await page.waitForTimeout(500);
await page.getByRole("button", { name: "Stop" }).first().click();
await page.waitForTimeout(1500);
const dlg = page.getByRole("dialog");
if (await dlg.count()) { console.log("stop dialog:", (await dlg.innerText()).replace(/\n+/g, " | ").slice(0, 300)); await dlg.getByRole("button", { name: /Stop/ }).last().click(); }
await page.waitForTimeout(3000);
console.log("after stop:", (await page.innerText("main")).replace(/\n+/g, " | ").slice(0, 500));
await page.screenshot({ path: `${OUT}/shot-research-after-stop.png`, fullPage: true });
console.log(JSON.stringify(summarize(log, "console-site8.json"), null, 1).split("\n").filter((l) => !/inline style/.test(l)).join("\n"));
await browser.close();
