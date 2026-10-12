const { chromium } = require('playwright');

async function takeScreenshots() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  
  const screenshotsDir = './screenshots';
  const fs = require('fs');
  if (!fs.existsSync(screenshotsDir)) {
    fs.mkdirSync(screenshotsDir);
  }
  
  // Auth pages (public)
  const publicPages = [
    { name: 'login', url: 'http://localhost:3000/login' },
    { name: 'operator-login', url: 'http://localhost:3000/operator/login' },
    { name: 'forgot-password', url: 'http://localhost:3000/forgot' },
    { name: 'verify-email', url: 'http://localhost:3000/verify-email' },
  ];
  
  for (const p of publicPages) {
    try {
      await page.goto(p.url, { waitUntil: 'networkidle', timeout: 30000 });
      await page.screenshot({ path: `${screenshotsDir}/${p.name}.png`, fullPage: true });
      console.log(`✓ ${p.name}`);
    } catch (e) {
      console.error(`✗ ${p.name}:`, e.message);
    }
  }
  
  // Login as client
  try {
    await page.goto('http://localhost:3000/login', { waitUntil: 'networkidle' });
    await page.getByLabel('Work email').fill('test@local.dev');
    await page.getByLabel('Password').fill('Workspace2026!');
    await page.getByRole('button', { name: 'Sign in to workspace' }).click();
    await page.waitForURL('http://localhost:3000/', { timeout: 30000 });
    console.log('✓ Client logged in');
    
    // Client pages
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
      try {
        await page.goto(p.url, { waitUntil: 'networkidle', timeout: 30000 });
        await page.screenshot({ path: `${screenshotsDir}/${p.name}.png`, fullPage: true });
        console.log(`✓ ${p.name}`);
      } catch (e) {
        console.error(`✗ ${p.name}:`, e.message);
      }
    }
    
    // Sign out
    await page.goto('http://localhost:3000/', { waitUntil: 'networkidle' });
    const signout = page.getByRole('button', { name: /Sign out/i }).first();
    if (await signout.isVisible()) {
      await signout.click();
    }
  } catch (e) {
    console.error('Client login failed:', e.message);
  }
  
  await browser.close();
  console.log(`\nDone. Screenshots in ${screenshotsDir}/`);
}

takeScreenshots();
