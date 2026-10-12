import { test } from '@playwright/test';
import { signIn } from './web/e2e/helpers';

const API = process.env.E2E_API_ORIGIN ?? 'http://127.0.0.1:8080';

test('capture all public pages', async ({ page }) => {
  const screenshotsDir = './screenshots';
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

test('capture client pages', async ({ page }) => {
  const screenshotsDir = './screenshots';
  const seeded = await (await page.request.get(`${API}/fake-idp/tenants`)).json();
  await signIn(page, seeded.slug_a, 'ana', 'analyst');
  
  const clientPages = [
    { name: 'client-overview', url: 'http://localhost:3000/' },
    { name: 'client-accounts', url: 'http://localhost:3000/accounts' },
    { name: 'client-chat', url: 'http://localhost:3000/chat' },
    { name: 'client-runs', url: 'http://localhost:3000/runs' },
    { name: 'client-alerts', url: 'http://localhost:3000/alerts' },
    { name: 'client-audit', url: 'http://localhost:3000/audit' },
    { name: 'client-usage', url: 'http://localhost:3000/usage' },
    { name: 'client-settings', url: 'http://localhost:3000/settings' },
    { name: 'client-settings-users', url: 'http://localhost:3000/settings/users' },
    { name: 'client-settings-alerts', url: 'http://localhost:3000/settings/alerts' },
    { name: 'client-settings-security', url: 'http://localhost:3000/settings/security' },
    { name: 'client-settings-sso', url: 'http://localhost:3000/settings/sso' },
    { name: 'client-settings-crm', url: 'http://localhost:3000/settings/crm' },
    { name: 'client-settings-connectors', url: 'http://localhost:3000/settings/connectors' },
    { name: 'client-settings-support', url: 'http://localhost:3000/settings/support' },
  ];
  
  for (const p of clientPages) {
    await page.goto(p.url, { waitUntil: 'networkidle' });
    await page.screenshot({ path: `${screenshotsDir}/${p.name}.png`, fullPage: true });
  }
});
