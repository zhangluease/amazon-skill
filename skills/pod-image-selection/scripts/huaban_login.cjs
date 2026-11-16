#!/usr/bin/env node
// Human login in an isolated visible browser; never reads an everyday browser profile.
const fs = require('node:fs/promises');
const path = require('node:path');
const { ensureEnvironment } = require('./environment.cjs');
const { desktopAvailability } = require('./human_verification.cjs');
const { defaultStateFile } = require('./huaban_image_search.cjs');
const { inside } = require('./sellersprite_session.cjs');

async function privateStateDirectory(destination) {
  await fs.mkdir(path.dirname(destination), { recursive: true, mode: 0o700 });
  const directory = await fs.realpath(path.dirname(destination));
  if (inside(directory, path.resolve(__dirname, '..'))) throw Error('HUABAN_LOGIN_UNSAFE_STATE_PATH');
  for (let parent = directory;; parent = path.dirname(parent)) {
    if (await fs.access(path.join(parent, '.git')).then(() => true).catch(() => false))
      throw Error('HUABAN_LOGIN_UNSAFE_STATE_PATH');
    if (parent === path.dirname(parent)) break;
  }
}

async function saveState(state, destination) {
  const filtered = {
    cookies: (state.cookies || []).filter(c => c.domain.replace(/^\./, '') === 'huaban.com'),
    origins: (state.origins || []).filter(o => o.origin === 'https://huaban.com')
  };
  await privateStateDirectory(destination);
  const temporary = destination + '.' + process.pid + '.tmp';
  try {
    await fs.writeFile(temporary, JSON.stringify(filtered), { mode: 0o600, flag: 'wx' });
    await fs.rename(temporary, destination);
    await fs.chmod(destination, 0o600);
  } finally { await fs.rm(temporary, { force: true }); }
}

async function login(options = {}) {
  if (!desktopAvailability().available) throw Error('HUABAN_LOGIN_INTERACTIVE_REQUIRED');
  const destination = path.resolve(options.state || defaultStateFile());
  const seconds = Number(options.timeout || 600);
  if (!Number.isInteger(seconds) || seconds < 30 || seconds > 900) throw Error('HUABAN_LOGIN_INVALID_TIMEOUT');
  await privateStateDirectory(destination);
  const environment = await ensureEnvironment({ out: path.join(path.dirname(destination), 'huaban-login-environment'),
    prepare: true, keepBrowser: true, headless: false });
  if (!environment.report.ready) throw Error('HUABAN_LOGIN_ENVIRONMENT_NOT_READY');
  const browser = environment.browser;
  try {
    const context = await browser.newContext({ locale: 'zh-CN', viewport: { width: 1440, height: 1100 } });
    const page = await context.newPage();
    await page.goto('https://huaban.com/discovery', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.getByRole('button', { name: '登录/注册', exact: true }).waitFor({ state: 'visible', timeout: 15000 });
    await page.bringToFront();
    process.stderr.write(JSON.stringify({ event: 'huaban_login', status: 'awaiting_login', timeout_seconds: seconds }) + '\n');
    const deadline = Date.now() + seconds * 1000;
    while (Date.now() < deadline) {
      if (!browser.isConnected() || page.isClosed()) throw Error('HUABAN_LOGIN_CANCELLED');
      const loginVisible = await page.getByRole('button', { name: '登录/注册', exact: true }).isVisible().catch(() => true);
      const searchVisible = await page.getByRole('textbox', { name: '搜索花瓣内容', exact: true }).isVisible().catch(() => false);
      if (!loginVisible && searchVisible) {
        await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 });
        await page.getByRole('button', { name: '以图搜索', exact: true }).waitFor({ state: 'visible', timeout: 15000 });
        const confirmed = await page.evaluate(() => Boolean(window.__NEXT_DATA__?.props?.initStore?.user?.user_id));
        if (confirmed) {
          await saveState(await context.storageState(), destination);
          return { event: 'huaban_login', status: 'saved', state_file: destination };
        }
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw Error('HUABAN_LOGIN_TIMEOUT');
  } finally { await browser.close().catch(() => {}); }
}

if (require.main === module) Promise.resolve().then(async () => {
  const options = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const key = process.argv[i].replace(/^--/, ''), value = process.argv[i + 1];
    if (!['state', 'timeout'].includes(key) || !value) throw Error('HUABAN_LOGIN_INVALID_ARGUMENT');
    options[key] = value;
  }
  console.log(JSON.stringify(await login(options)));
}).catch(error => {
  const category = /^HUABAN_LOGIN_[A-Z_]+$/.test(error.message) ? error.message : 'HUABAN_LOGIN_FAILED';
  console.error(JSON.stringify({ event: 'huaban_login', status: 'failed', category }));
  process.exitCode = 2;
});

module.exports = { login, saveState };
