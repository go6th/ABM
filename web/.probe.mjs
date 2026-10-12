import { chromium } from '@playwright/test';
const [base, w, h, ...paths] = process.argv.slice(2);
const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: +w, height: +h } }); const p = await ctx.newPage();
await p.goto(base + '/login'); await p.getByLabel('Work email').fill('test@local.dev'); await p.getByLabel('Password', { exact: true }).fill('Workspace2026!');
await p.getByRole('button', { name: /sign in/i }).click(); await p.waitForURL((u) => !u.pathname.startsWith('/login'));
for (const path of paths) {
  await p.goto(base + path, { waitUntil: 'networkidle' });
  console.log(path, await p.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const wide = [...document.querySelectorAll('body *')].filter((e) => e.getBoundingClientRect().right > vw + 1)
      .slice(0, 8).map((e) => `${e.tagName}.${[...e.classList].join('.')} r=${Math.round(e.getBoundingClientRect().right)} w=${Math.round(e.getBoundingClientRect().width)}`);
    const tall = [...document.body.children].map((e) => `${e.tagName}.${e.className} h=${Math.round(e.getBoundingClientRect().height)} pos=${getComputedStyle(e).position}`);
    const convs = document.querySelector('.ws-convs'); const mn=document.querySelector('main'); const cs=getComputedStyle(mn); const bd=getComputedStyle(document.querySelector('.ws-body')); const extra={mainW:cs.width,mainMargin:cs.margin,mainMax:cs.maxWidth,mainDisplay:cs.display, bodyCols:bd.gridTemplateColumns, convW: convs&&getComputedStyle(convs).width, convMargin: convs&&getComputedStyle(convs).margin, convMax: convs&&getComputedStyle(convs).maxWidth};
    return { vw, scrollW: document.documentElement.scrollWidth, scrollH: document.documentElement.scrollHeight, wide, convCols: convs && getComputedStyle(convs).gridTemplateColumns, extra };
  }));
}
await b.close();
