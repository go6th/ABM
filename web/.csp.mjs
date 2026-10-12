import { chromium } from '@playwright/test';
const [base, ...paths] = process.argv.slice(2);
const b = await chromium.launch(); const p = await (await b.newContext()).newPage();
let n = 0; p.on('console', (msg) => { if (msg.text().includes('inline style')) n++; });
await p.goto(base + '/login', { waitUntil: 'networkidle' }); console.log('/login', n); n = 0;
await p.getByLabel('Work email').fill('test@local.dev'); await p.getByLabel('Password', { exact: true }).fill('Workspace2026!');
await p.getByRole('button', { name: /sign in/i }).click(); await p.waitForURL((u) => !u.pathname.startsWith('/login')); await p.waitForTimeout(800); console.log('after login', n); n=0;
for (const path of paths) { await p.goto(base + path, { waitUntil: 'networkidle' }); await p.waitForTimeout(500);
  const attrs = await p.evaluate(() => [...document.querySelectorAll('[style]')].slice(0,5).map(e => e.tagName + '.' + e.className + ' ' + e.getAttribute('style')));
  console.log(path, n, attrs); n = 0; }
await b.close();
