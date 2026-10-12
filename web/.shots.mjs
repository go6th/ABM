import { chromium } from '@playwright/test';
const [base, out, sizes, ...paths] = process.argv.slice(2);
const b = await chromium.launch();
for (const spec of sizes.split(',')) {
  const [w, h, scheme] = spec.split('x');
  const ctx = await b.newContext({ viewport: { width: +w, height: +h }, colorScheme: scheme || 'light' });
  const p = await ctx.newPage();
  const errs = []; p.on('console', (msg) => { if (msg.type() === 'error') errs.push(msg.text().slice(0, 160)); });
  p.on('pageerror', (e) => errs.push('pageerror ' + e.message.slice(0, 160)));
  await p.goto(base + '/login'); await p.getByLabel('Work email').fill('test@local.dev');
  await p.getByLabel('Password', { exact: true }).fill('Workspace2026!');
  await p.getByRole('button', { name: /sign in/i }).click();
  await p.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 }).catch(() => {});
  for (const path of paths) {
    await p.goto(base + path, { waitUntil: 'networkidle' }).catch(() => {});
    await p.waitForTimeout(600);
    const f = `${out}/${w}${scheme || ''}${path.replace(/[^a-z0-9]+/gi, '_')}.png`;
    await p.screenshot({ path: f, fullPage: true });
  }
  console.log(spec, 'errors:', [...new Set(errs)].slice(0, 6));
  await ctx.close();
}
await b.close();
