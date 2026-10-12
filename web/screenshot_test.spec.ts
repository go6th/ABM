import { test } from '@playwright/test';

const API = process.env.E2E_API_ORIGIN ?? 'http://127.0.0.1:8080';

test('capture public pages', async ({ page }) => {
  const screenshotsDir = './screenshots';
  const fs = await import('fs');
  if (!fs.existsSync(screenshotsDir)) {
    fs.mkdirSync(screenshotsDir);
  }
  const publicPages = [
    { name: 'login', url: 'http://localhost:3000/login' },
    { name: 'operator-login', url: 'http://localhost:3000/operator/login' },
    { name: 'forgot-password', url: 'http://localhost:3000/forgot' },
    { name: 'verify-email', url: 'http://localhost:3000/verify-email' },
  ];
  
  for (const p of publicPages) {
    await page.goto(p.url, { waitUntil: 'networkidle' });
    await page.screenshot({ path: `${screenshotsDir}/${p.name}.png`, fullPage: true });
  }
});
