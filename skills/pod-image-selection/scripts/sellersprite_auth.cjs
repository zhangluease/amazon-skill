const fs = require('node:fs/promises');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { ensureEnvironment } = require('./environment.cjs');
const { desktopAvailability } = require('./human_verification.cjs');
const { prepareExtension, fail } = require('./sellersprite_session.cjs');

function isAmazon(url) {
  try { return /(^|\.)(?:amazon\.[a-z.]+|amazon-adsystem\.com)$/.test(new URL(url).hostname); }
  catch { return false; }
}

function sessionFromBridge(value, version) {
  if (!value || value.extension_version !== version || value.format !== 'sellersprite-http-session-v1') return null;
  const keep = {};
  for (const k of ['auth-token', 'auth-fp', 'random-token', 'user-agent']) {
    const v = value.headers?.[k];
    if (typeof v === 'string' && v.length && v.length <= 8192 && !/[\r\n]/.test(v)) keep[k] = v;
  }
  if (!['auth-token', 'auth-fp', 'random-token'].every(k => keep[k])) return null;
  const parameters = {};
  for (const k of ['version', 'language', 'extension', 'source']) {
    const v = value.parameters?.[k];
    if (typeof v === 'string' && v && v.length <= 200) parameters[k] = v;
  }
  parameters.version = version;
  return { format: 'sellersprite-http-session-v1', extension_version: version,
    captured_at: new Date().toISOString(), headers: keep, parameters };
}

async function saveSession(home, session) {
  const target = path.join(home.directory, 'http-session.json');
  const temporary = target + '.new';
  await fs.rm(temporary, { force: true });
  const file = await fs.open(temporary, 'wx', 0o600);
  try { await file.writeFile(JSON.stringify(session)); await file.sync(); }
  finally { await file.close(); }
  await fs.rename(temporary, target);
  await fs.chmod(target, 0o600).catch(() => {});
  return target;
}

async function authorize(home, options, publish, dependencies = {}) {
  const extension = await prepareExtension(home, options['extension-dir'], dependencies.extension || {});
  if (!extension.id) throw fail('EXTENSION_ID_UNAVAILABLE');
  const environment = await (dependencies.prepare || ensureEnvironment)({ out: path.join(options.out, 'environment'),
    prepare: true, browserPreference: 'chromium' });
  if (!environment.report.ready) throw fail('ENVIRONMENT_NOT_READY');
  const env = { ...process.env };
  if (env.POD_IMAGE_SELECTION_INTERACTIVE !== undefined) env.POD_HUABAN_INTERACTIVE = env.POD_IMAGE_SELECTION_INTERACTIVE;
  const desktop = (dependencies.desktop || (() => desktopAvailability({ env })))();
  let context, bridge, captured;
  const launch = async headed => {
    captured = null;
    context = await (dependencies.launch || ((profile, settings) => environment.playwright.chromium.launchPersistentContext(profile, settings)))(home.profile,
      { channel: 'chromium', headless: !headed, locale: 'zh-CN', viewport: { width: 1200, height: 900 },
        args: [`--disable-extensions-except=${extension.directory}`, `--load-extension=${extension.directory}`] });
    // Authorization does not need Amazon. Prevent the plugin's default-home links from navigating there.
    await context.route('**/*', route => isAmazon(route.request().url()) ? route.abort() : route.fallback());
    bridge = await context.newPage();
    await bridge.goto(`chrome-extension://${extension.id}/${extension.manifest.action.default_popup}`, { waitUntil: 'load' });
  };
  const probe = async first => {
    // Use the original extension's messages in its own dedicated profile. Do not read browser databases.
    // Only the dedicated extension's selected device keys and refreshed token form the private cache.
    const result = await bridge.evaluate(async first => {
      const send = action => new Promise(resolve => {
        const timer = setTimeout(() => resolve({ code: 'MESSAGE_TIMEOUT' }), 20000);
        chrome.runtime.sendMessage(action, response => {
          clearTimeout(timer);
          resolve(chrome.runtime.lastError ? { code: 'MESSAGE_FAILED' } : response || {});
        });
      });
      const session = async token => {
        for (let i = 0; i < 10; i++) {
          const device = await chrome.storage.local.get(['__FP', '__UUID', '__LANGUAGE', '__SOURCE']);
          if (device.__FP?.value && device.__FP.expire > Date.now() && device.__UUID) {
            const version = chrome.runtime.getManifest().version;
            return { format: 'sellersprite-http-session-v1', extension_version: version,
              headers: { 'auth-token': token, 'auth-fp': device.__FP.value,
                'random-token': device.__UUID, 'user-agent': navigator.userAgent },
              parameters: { version, extension: chrome.runtime.id,
                language: device.__LANGUAGE || 'zh_CN', source: device.__SOURCE } };
          }
          await new Promise(resolve => setTimeout(resolve, 300));
        }
        return null;
      };
      if (first) {
        const current = await send({ action: 'CLIENT_LATEST', args: {} });
        if (current.code === 'OK' && current.data?.token) return { plugin_ready: true, session: await session(current.data.token) };
      }
      const web = await send({ action: 'AUTO_SIGN_IN_INFO', args: {} });
      if (!web.data?.token || web.data.token === globalThis.__podLastSyncToken) return { plugin_ready: false };
      globalThis.__podLastSyncToken = web.data.token;
      const sync = await send({ action: 'SIGN_IN_WITH_TOKEN', args: { token: web.data.token } });
      if (sync.code !== 'OK') return { plugin_ready: false, rejected: true };
      // The vendor acknowledges sign-in before its asynchronous local store is necessarily committed.
      // Recheck a bounded number of times in this same browser instead of closing it immediately.
      for (let i = 0; i < 3; i++) {
        const current = await send({ action: 'CLIENT_LATEST', args: {} });
        if (current.code === 'OK' && current.data?.token)
          return { plugin_ready: true, session: await session(current.data.token) };
        await new Promise(resolve => setTimeout(resolve, 300));
      }
      return { plugin_ready: false, rejected: true };
    }, first);
    captured = sessionFromBridge(result.session, extension.manifest.version);
    return result;
  };
  try {
    await launch(false);
    let state = await probe(true);
    if (state.plugin_ready && !captured) throw fail('AUTHORIZATION_BRIDGE_UNAVAILABLE');
    if (state.plugin_ready && captured) {
      const target = await saveSession(home, captured);
      await publish('reused', { session_saved: true });
      return target;
    }
    if (!desktop.available) {
      await publish('requires_interactive_browser', { reason: desktop.reason });
      throw fail('INTERACTIVE_BROWSER_REQUIRED');
    }
    await context.close();
    await launch(true);
    const login = await context.newPage();
    await login.goto('https://www.sellersprite.com/cn/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await login.bringToFront();
    await publish('awaiting_login', { timeout_seconds: options.timeout,
      message: '请在专用浏览器登录卖家精灵官网，保持窗口打开；脚本会同步插件授权并继续接口查询，无需打开Amazon。' });
    const deadline = Date.now() + options.timeout * 1000;
    let first = true;
    while (Date.now() < deadline) {
      if (login.isClosed() || bridge.isClosed()) throw fail('AUTHORIZATION_CANCELLED');
      state = await probe(first); first = false;
      if (state.plugin_ready && !captured) throw fail('AUTHORIZATION_BRIDGE_UNAVAILABLE');
      if (state.rejected) throw fail('AUTHORIZATION_REJECTED');
      if (state.plugin_ready && captured) {
        const target = await saveSession(home, captured);
        await publish('completed', { session_saved: true });
        return target;
      }
      await delay(800);
    }
    throw fail('AUTHORIZATION_TIMEOUT');
  } catch (error) {
    if (bridge?.isClosed()) throw fail('AUTHORIZATION_CANCELLED');
    throw error;
  } finally {
    await context?.close().catch(() => {});
  }
}

module.exports = { authorize, sessionFromBridge, saveSession, isAmazon };
