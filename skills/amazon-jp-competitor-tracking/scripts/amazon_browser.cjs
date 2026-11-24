// Amazon-only fallback. Uses a dedicated native Chromium profile; no stealth patches.
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { ensureEnvironment, findPython } = require('./environment.cjs');
const ORIGIN = 'https://www.amazon.co.jp';
const BLOCKED = new Set(['AMAZON_CONTINUE_SHOPPING_REQUIRED', 'AMAZON_HUMAN_VERIFICATION_REQUIRED',
  'AMAZON_HTTP_403', 'AMAZON_HTTP_429']);
const fail = code => Object.assign(new Error(code), { code });

async function privateJson(file, data) {
  const temporary = `${file}.${process.pid}.tmp`;
  try {
    await fs.writeFile(temporary, JSON.stringify(data, null, 2), { mode: 0o600, flag: 'wx' });
    await fs.rename(temporary, file);
  } finally { await fs.unlink(temporary).catch(() => {}); }
}

async function pageState(page) {
  return page.evaluate(() => {
    const captcha = [...document.querySelectorAll('form')].some(form => /captcha/i.test(form.getAttribute('action') || ''));
    if (document.getElementById('captchacharacters') || document.title.trim().toLowerCase() === 'robot check')
      return 'AMAZON_HUMAN_VERIFICATION_REQUIRED';
    if (captcha) return /ショッピングを続け|Continue shopping|继续购物|繼續購物/i.test(document.body?.innerText || '')
      ? 'AMAZON_CONTINUE_SHOPPING_REQUIRED' : 'AMAZON_HUMAN_VERIFICATION_REQUIRED';
    return document.getElementById('productTitle') ? 'product' : 'pending';
  });
}

async function waitForProduct(page, { headed, verificationTimeout, notify }) {
  let state = await pageState(page);
  if (BLOCKED.has(state)) {
    if (!headed) throw fail(state);
    notify({ event: 'amazon_verification', status: 'waiting_for_user', category: state,
      timeout_seconds: verificationTimeout });
  }
  if (state !== 'product') {
    try {
      await page.waitForFunction(() => Boolean(document.getElementById('productTitle'))
        && ![...document.querySelectorAll('form')].some(form => /captcha/i.test(form.getAttribute('action') || '')),
      null, { timeout: BLOCKED.has(state) ? verificationTimeout * 1000 : 15000, polling: 1000 });
    } catch {
      state = await pageState(page).catch(() => 'pending');
      throw fail(BLOCKED.has(state) ? state : 'AMAZON_PRODUCT_NOT_CONFIRMED');
    }
  }
}

async function selectJapan(page) {
  // Read current page CSRF values in memory and use the page's own same-origin fetch.
  // Neither request headers nor raw response bodies are written to evidence.
  const selected = await page.evaluate(async () => {
    const invalid = () => ({ status: 'failed', category: 'AMAZON_DESTINATION_NOT_CONFIRMED' });
    try {
      const modal = JSON.parse(document.getElementById('nav-global-location-data-modal-action')?.getAttribute('data-a-modal') || 'null');
      const relative = modal?.url;
      const token = modal?.ajaxHeaders?.['anti-csrftoken-a2z'];
      if (typeof relative !== 'string' || !relative.startsWith('/') || relative.startsWith('//')
        || new URL(relative, location.origin).origin !== 'https://www.amazon.co.jp'
        || typeof token !== 'string' || !token || token.length > 4096 || /[\r\n]/.test(token)) return invalid();
      const headers = { 'anti-csrftoken-a2z': token, 'X-Requested-With': 'XMLHttpRequest' };
      const response = await fetch(relative, { headers, credentials: 'same-origin', redirect: 'error', signal: AbortSignal.timeout(15000) });
      if ([403, 429].includes(response.status)) return { status: 'failed', category: `AMAZON_HTTP_${response.status}` };
      if (!response.ok) return invalid();
      const text = await response.text();
      const match = text.match(/\bCSRF_TOKEN\s*:\s*("(?:\\.|[^"\\])*")/);
      const updateToken = match ? JSON.parse(match[1]) : null;
      if (typeof updateToken !== 'string' || !updateToken || updateToken.length > 4096 || /[\r\n]/.test(updateToken)) return invalid();
      const store = new URL(relative, location.origin).searchParams.get('storeContext') || 'generic';
      const update = await fetch('/portal-migration/hz/glow/address-change?actionSource=glow', {
        method: 'POST', credentials: 'same-origin', redirect: 'error', signal: AbortSignal.timeout(15000),
        headers: { ...headers, 'anti-csrftoken-a2z': updateToken, 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ storeContext: store, deviceType: 'web', pageType: 'Detail', actionSource: 'glow',
          locationType: 'COUNTRY', district: 'JP', countryCode: 'JP' }),
      });
      if ([403, 429].includes(update.status)) return { status: 'failed', category: `AMAZON_HTTP_${update.status}` };
      if (!update.ok) return invalid();
      const data = await update.json();
      if (data.sembuUpdated !== 1 || data.address?.countryCode !== 'JP') return invalid();
      return { status: 'ok', expected: { country_code: 'JP', country_name: '日本', selection_type: 'country',
        postal_code: null, resolved_postal_code: data.address.zipCode || null,
        selection_method: 'browser_country_change_api', verified: false } };
    } catch { return invalid(); }
  });
  if (selected.status !== 'ok') throw fail(selected.category);
  return selected.expected;
}

function parsePage(python, asin, html, expected, headed) {
  return new Promise((resolve, reject) => {
    const worker = spawn(python.command, [...python.prefix, path.join(__dirname, 'amazon_product.py'), 'parse-browser', asin],
      { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    let stdout = '';
    const timer = setTimeout(() => { worker.kill(); reject(fail('AMAZON_PARSER_TIMEOUT')); }, 15000);
    worker.stdout.on('data', data => { stdout += data; if (stdout.length > 1024 * 1024) worker.kill(); });
    worker.stderr.resume();
    worker.stdin.on('error', () => {});
    worker.on('error', () => { clearTimeout(timer); reject(fail('AMAZON_PARSER_FAILED')); });
    worker.on('close', code => {
      clearTimeout(timer);
      try { if (code !== 0) throw Error(); resolve(JSON.parse(stdout)); }
      catch { reject(fail('AMAZON_PARSER_FAILED')); }
    });
    worker.stdin.end(JSON.stringify({ html, expected_destination: expected, headed }));
  });
}

async function collectAmazon(options, dependencies = {}) {
  const { asin, stateDir, mode = 'headless', verificationTimeout = 300 } = options;
  if (!/^[A-Z0-9]{10}$/.test(asin) || !['headless', 'headed'].includes(mode)
    || !Number.isInteger(verificationTimeout) || verificationTimeout < 5 || verificationTimeout > 900)
    throw fail('INVALID_AMAZON_BROWSER_ARGUMENT');
  const directory = path.resolve(stateDir);
  if (!await fs.stat(path.join(directory, '.amazon-jp-competitor-tracking')).then(s => s.isFile()).catch(() => false))
    throw fail('STATE_DIRECTORY_NOT_MANAGED');
  const gate = path.join(directory, 'amazon-browser-status.json');
  const profile = path.join(directory, 'amazon-profile');
  const destinationFile = path.join(profile, 'amazon-destination.json');
  if (await fs.lstat(profile).then(s => s.isSymbolicLink()).catch(() => false)) throw fail('UNSAFE_STATE_DIRECTORY');
  const previous = await fs.readFile(gate, 'utf8').then(JSON.parse).catch(() => null);
  if (mode === 'headless' && previous?.status === 'verification_required')
    return { status: 'verification_required', category: previous.category, endpoint: ORIGIN + '/dp/' + asin,
      not_attempted: true, browser_started: false, requires_visible_browser: previous.category !== 'AMAZON_HTTP_429',
      delivery_destination: { country_code: 'JP', verified: false } };
  let context;
  const headed = mode === 'headed';
  const notify = dependencies.notify || (event => process.stderr.write(JSON.stringify(event) + '\n'));
  const base = { endpoint: ORIGIN + '/dp/' + asin, checked_at: new Date().toISOString(),
    collection_method: `browser_${mode}`, browser_started: false };
  try {
    const environment = await (dependencies.ensureEnvironment || ensureEnvironment)({ prepare: true });
    await fs.mkdir(profile, { recursive: true, mode: 0o700 });
    context = await (dependencies.launch || ((dir, settings) => environment.playwright.chromium.launchPersistentContext(dir, settings)))(profile,
      { channel: 'chromium', headless: !headed, locale: 'ja-JP', timezoneId: 'Asia/Tokyo', timeout: 15000 });
    base.browser_started = true;
    const page = context.pages()[0] || await context.newPage();
    const response = await page.goto(base.endpoint, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if ([403, 429].includes(response?.status())) throw fail(`AMAZON_HTTP_${response.status()}`);
    await waitForProduct(page, { headed, verificationTimeout, notify });
    if (new URL(page.url()).origin !== ORIGIN) throw fail('AMAZON_ENDPOINT_NOT_ALLOWED');
    let expected = await fs.readFile(destinationFile, 'utf8').then(JSON.parse).catch(() => null);
    const python = (dependencies.findPython || findPython)();
    const parse = dependencies.parsePage || parsePage;
    let snapshot = await parse(python, asin, await page.content(), expected, headed);
    if (snapshot.category === 'AMAZON_DESTINATION_NOT_CONFIRMED') {
      expected = await (dependencies.selectJapan || selectJapan)(page);
      await privateJson(destinationFile, expected);
      const refresh = await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 });
      if ([403, 429].includes(refresh?.status())) throw fail(`AMAZON_HTTP_${refresh.status()}`);
      await waitForProduct(page, { headed, verificationTimeout, notify });
      snapshot = await parse(python, asin, await page.content(), expected, headed);
    }
    if (snapshot.status !== 'ok') throw fail(snapshot.category);
    if (snapshot.data?.asin !== asin || snapshot.delivery_destination?.country_code !== 'JP'
      || snapshot.delivery_destination?.verified !== true) throw fail('AMAZON_PRODUCT_NOT_CONFIRMED');
    await privateJson(gate, { status: 'ready', checked_at: new Date().toISOString() });
    return snapshot;
  } catch (error) {
    const category = typeof error.code === 'string' && /^[A-Z_0-9]{1,80}$/.test(error.code)
      ? error.code : 'AMAZON_BROWSER_FAILED';
    const result = { ...base, status: BLOCKED.has(category) ? 'verification_required' : 'failed', category,
      requires_visible_browser: BLOCKED.has(category) && category !== 'AMAZON_HTTP_429',
      delivery_destination: { country_code: 'JP', verified: false } };
    if (BLOCKED.has(category)) await privateJson(gate, result);
    return result;
  } finally { await context?.close().catch(() => {}); }
}

module.exports = { collectAmazon, pageState, waitForProduct, selectJapan, parsePage };

if (require.main === module) {
  const [asin, stateDir, mode = 'headless', timeout = '300'] = process.argv.slice(2);
  collectAmazon({ asin, stateDir, mode, verificationTimeout: Number(timeout) })
    .then(result => process.stdout.write(JSON.stringify(result) + '\n'))
    .catch(error => { process.stdout.write(JSON.stringify({ status: 'failed', category: /^[A-Z_0-9]+$/.test(error.code || '')
      ? error.code : 'AMAZON_BROWSER_FAILED' }) + '\n'); process.exitCode = 2; });
}
