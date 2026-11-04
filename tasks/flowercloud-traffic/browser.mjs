import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import { parseUsage } from './flow.mjs';

/** 独立无头 Chrome 读取固定产品；只在登录过期时用配置认证，不操作桌面或套餐。 */
export async function readUsage(config, root) {
  const target = new URL(config.product_url);
  if (target.origin !== 'https://api-flowercloud.com' ||
      target.pathname !== '/clientarea.php' ||
      target.searchParams.get('action') !== 'productdetails' ||
      target.searchParams.get('id') !== '280585') throw new Error('未授权的产品地址');
  const profile = path.join(root, 'browser-profile');
  await fs.mkdir(profile, { recursive: true, mode: 0o700 });
  await fs.chmod(profile, 0o700);
  const context = await chromium.launchPersistentContext(profile, {
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: true, chromiumSandbox: true, timeout: 30000,
    ignoreDefaultArgs: ['--password-store=basic', '--use-mock-keychain'],
    viewport: { width: 1280, height: 900 },
  });
  // 外层超时或人为停止时先关闭该任务浏览器，保留用户桌面 Chrome。
  const stop = () => { void context.close().finally(() => process.exit(143)); };
  process.once('SIGTERM', stop);
  try {
    const page = context.pages()[0] || await context.newPage();
    page.setDefaultTimeout(20000);
    await page.goto(target.href, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (new URL(page.url()).origin !== target.origin) throw new Error('登录页面跳转至未授权站点');
    if (await page.locator('#inputPassword').isVisible()) {
      const form = page.locator('form').filter({ has: page.locator('#inputPassword') });
      const submit = form.locator('button[type="submit"], input[type="submit"]');
      if (await submit.count() !== 1) throw new Error('登录按钮无法唯一确认');
      await page.locator('#inputEmail').fill(config.username);
      await page.locator('#inputPassword').fill(config.password);
      await submit.click();
      // 每轮最多提交一次登录，防止凭据过期或验证码时反复尝试。
      await page.locator('#inputPassword').waitFor({ state: 'hidden', timeout: 20000 });
      await page.goto(target.href, { waitUntil: 'domcontentloaded', timeout: 30000 });
    }
    const current = new URL(page.url());
    if (current.origin !== target.origin || current.pathname !== target.pathname ||
        current.searchParams.get('id') !== target.searchParams.get('id')) {
      throw new Error('未进入指定产品，认证可能已失效');
    }
    const heading = page.getByRole('heading', { name: '流量使用', exact: true });
    await heading.waitFor({ state: 'visible', timeout: 25000 });
    const text = await heading.evaluate(el => el.parentElement.innerText);
    return { ...parseUsage(text), captured_at: new Date().toISOString(), product_id: '280585' };
  } finally {
    process.removeListener('SIGTERM', stop);
    await context.close();
  }
}
