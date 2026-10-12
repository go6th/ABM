import { chromium } from '@playwright/test';
const [base, out] = process.argv.slice(2);
const b = await chromium.launch();
for (const [w, h] of [[1440, 900], [390, 844]]) {
  const p = await (await b.newContext({ viewport: { width: w, height: h } })).newPage();
  const errs = []; p.on('pageerror', (e) => errs.push(e.message));
  const r = await p.goto(base + '/__design', { waitUntil: 'networkidle' }); console.log('status', r.status());
  await p.screenshot({ path: `${out}/gal-${w}.png`, fullPage: true });
  await p.getByRole('button', { name: 'Source 2, Probable' }).click(); await p.waitForTimeout(400);
  await p.screenshot({ path: `${out}/gal-${w}-drawer.png` });
  await p.keyboard.press('Escape'); await p.waitForTimeout(200);
  console.log('focus back on chip:', await p.evaluate(() => document.activeElement?.getAttribute('aria-label')));
  await p.getByRole('button', { name: 'Dark' }).click(); await p.waitForTimeout(200);
  await p.screenshot({ path: `${out}/gal-${w}-dark.png`, fullPage: true });
  console.log(w, errs);
}
await b.close();
