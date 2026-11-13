#!/usr/bin/env node
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { setTimeout: delay } = require('node:timers/promises');
const { ensureEnvironment } = require('./environment.cjs');
const { desktopAvailability } = require('./human_verification.cjs');
const { stateRoot, fail, prepareHome, prepareExtension } = require('./sellersprite_session.cjs');
const { SellerSpriteUI } = require('./sellersprite_ui.cjs');
const execute = promisify(execFile);
const REMEDIES = {
  EXTENSION_NOT_FOUND: '请先在本机Chrome/Edge安装卖家精灵插件，或用 --extension-dir 指定已安装插件的程序目录。',
  INTERACTIVE_BROWSER_REQUIRED: '请在本机或用户可操作的远程桌面运行首次授权；无桌面的容器无法显示登录窗口。',
  PROFILE_BUSY: '专用配置已有任务运行，请等待其结束；不要删除正在使用的浏览器配置。',
  AUTHORIZATION_TIMEOUT: '登录等待超时，配置已保留。重新运行相同查询可继续授权。',
  AUTHORIZATION_CANCELLED: '登录窗口已关闭，配置已保留。准备好后重新运行相同查询。',
  INCOMPLETE_TABLE_COLLECTION: '未能读取全部已加载商品，请反馈browser-status.json并核对插件版本。',
  QUERY_RESULT_NOT_CONFIRMED: '未确认新关键词查询已完成，请核对插件界面和网络后重试。',
  MARKETPLACE_NOT_US: 'Amazon跳转到了其他站点，请在专用浏览器切回Amazon.com美国站后重试。',
  PLUGIN_QUERY_REJECTED: '插件提示查询失败，请在专用浏览器核对账号权限或网络；不将失败当成零结果。',
};

function argumentsFor(argv) {
  const options = { command: argv[0] && !argv[0].startsWith('--') ? argv.shift() : 'search', timeout: 300 };
  if (!['check', 'prepare', 'login', 'search'].includes(options.command)) throw fail('INVALID_COMMAND');
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i].replace(/^--/, '');
    if (key === 'headless') { options.headless = true; continue; }
    if (!['query', 'out', 'extension-dir', 'state-dir', 'authorization-timeout'].includes(key) || !argv[i + 1]) throw fail('INVALID_ARGUMENT');
    options[key] = argv[++i];
  }
  options.timeout = Number(options['authorization-timeout'] || 300);
  if (!Number.isInteger(options.timeout) || options.timeout < 5 || options.timeout > 900) throw fail('INVALID_TIMEOUT');
  if (options.command === 'search' && (!options.query?.trim() || options.query.length > 500)) throw fail('INVALID_QUERY');
  options.query = options.query?.trim();
  if (!options.out) throw fail('MISSING_OUTPUT');
  return options;
}
function desktop() {
  const env = { ...process.env };
  if (env.POD_IMAGE_SELECTION_INTERACTIVE !== undefined) env.POD_HUABAN_INTERACTIVE = env.POD_IMAGE_SELECTION_INTERACTIVE;
  return desktopAvailability({ env });
}
async function run(options, dependencies = {}) {
  const output = path.resolve(options.out), started = Date.now();
  await fs.mkdir(output, { recursive: true });
  let home, context, ui, page, headless = !!options.headless, handoffs = 0, stage = 'preflight';
  const notify = dependencies.notify || (value => process.stderr.write(JSON.stringify(value) + '\n'));
  const publish = async (status, details = {}) => {
    const report = { status, ...details };
    await fs.writeFile(path.join(output, 'authorization.json'), JSON.stringify(report, null, 2) + '\n');
    notify({ event: 'seller_authorization', ...report });
  };
  try {
    home = await prepareHome(options['state-dir'] || stateRoot(), output);
    const extension = await prepareExtension(home, options['extension-dir'], dependencies.extension || {});
    const environment = await (dependencies.prepare || ensureEnvironment)({ out: path.join(output, 'environment'),
      prepare: options.command !== 'check', browserPreference: 'chromium' });
    if (!environment.report.ready) throw fail('ENVIRONMENT_NOT_READY');
    if (['check', 'prepare'].includes(options.command)) {
      const status = { status: 'ready', extension_version: extension.manifest.version,
        session_tested: false, profile_directory: home.profile };
      await fs.writeFile(path.join(output, 'browser-status.json'), JSON.stringify(status, null, 2));
      return status;
    }
    const access = (dependencies.desktop || desktop)();
    if (!headless && !access.available) {
      await publish('requires_interactive_browser', { reason: access.reason });
      throw fail('INTERACTIVE_BROWSER_REQUIRED');
    }
    const launch = async visible => {
      stage = 'opening_browser';
      context = await (dependencies.launch || ((profile, settings) => environment.playwright.chromium.launchPersistentContext(profile, settings)))(home.profile,
        { channel: 'chromium', headless: !visible, locale: 'zh-CN', viewport: { width: 1500, height: 1000 },
          args: [`--disable-extensions-except=${extension.directory}`, `--load-extension=${extension.directory}`] });
      page = context.pages()[0] || await context.newPage();
      ui = dependencies.driver ? dependencies.driver(context, page) : new SellerSpriteUI(context, page);
      await ui.open();
      headless = !visible;
    };
    const authorize = async reason => {
      stage = 'authorization';
      if (handoffs++) throw fail('AUTHORIZATION_REPEATED');
      if (!access.available) {
        await publish('requires_interactive_browser', { reason: access.reason });
        throw fail('INTERACTIVE_BROWSER_REQUIRED');
      }
      if (headless) { await context.close(); await launch(true); }
      await page.bringToFront();
      await publish('awaiting_login', { reason, timeout_seconds: options.timeout,
        message: '请在专用浏览器中登录或同步卖家精灵账号，保持窗口打开；完成后会自动继续。' });
      const deadline = Date.now() + options.timeout * 1000;
      while (Date.now() < deadline) {
        const state = await ui.authorization().catch(() => 'loading');
        if (state === 'cancelled' || page.isClosed()) throw fail('AUTHORIZATION_CANCELLED');
        if (state === 'ready') { await publish('completed'); return; }
        await delay(400);
      }
      throw fail('AUTHORIZATION_TIMEOUT');
    };
    const cleanup = async () => { await context?.close().catch(() => {}); await home?.release(); };
    const interrupt = () => { cleanup().finally(() => process.exit(130)); };
    process.once('SIGINT', interrupt); process.once('SIGTERM', interrupt);
    try {
      await launch(!headless);
      let state = await ui.authorization();
      // Observe only the current page while the plugin initializes; no repeated HTTP probes.
      for (let i = 0; state === 'loading' && i < 20; i++) { await delay(250); state = await ui.authorization(); }
      if (state !== 'ready') await authorize(state);
      else await publish('reused');
      if (options.command === 'login') {
        const status = { status: 'authorized', profile_directory: home.profile, extension_version: extension.manifest.version };
        await fs.writeFile(path.join(output, 'browser-status.json'), JSON.stringify(status, null, 2));
        return status;
      }
      let data;
      stage = 'keyword_query';
      try { data = await ui.query(options.query); }
      catch (error) {
        if (error.code !== 'AUTHORIZATION_EXPIRED') throw error;
        await authorize('session_expired');
        data = await ui.query(options.query);
      }
      const input = path.join(output, 'browser-products.json');
      await fs.writeFile(input, JSON.stringify(data, null, 2) + '\n');
      const python = environment.python;
      stage = 'normalization';
      const normalized = await execute(python.command, [...python.prefix,
        path.join(__dirname, 'sellersprite_plugin_client.py'), 'normalize', '--query', options.query,
        '--input', input, '--out', output], { timeout: 30000, maxBuffer: 1024 * 1024 });
      const normalizedSummary = JSON.parse(normalized.stdout);
      if (!normalizedSummary.products && data.items.length) throw fail('NO_VALID_PRODUCTS');
      if (normalizedSummary.products) await execute(python.command, [...python.prefix, path.join(__dirname, 'render_product_gallery.py'),
        path.join(output, 'ranked-products.json')], { timeout: 30000, maxBuffer: 1024 * 1024 });
      const summary = { ...normalizedSummary, elapsed_ms: Date.now() - started,
        collection_method: 'persistent_browser_ui', browser_headless: headless,
        authorization_handoffs: handoffs, profile_directory: home.profile };
      await fs.writeFile(path.join(output, 'browser-status.json'), JSON.stringify(summary, null, 2));
      return summary;
    } finally {
      process.removeListener('SIGINT', interrupt); process.removeListener('SIGTERM', interrupt);
    }
  } catch (error) {
    const code = typeof error.code === 'string' && /^[A-Z_0-9]{1,80}$/.test(error.code) ? error.code : 'BROWSER_COLLECTION_FAILED';
    if (/AUTHORIZATION_(?:CANCELLED|TIMEOUT|REPEATED)/.test(code)) await publish(code.replace('AUTHORIZATION_', '').toLowerCase());
    await fs.writeFile(path.join(output, 'browser-status.json'), JSON.stringify({ status: 'failed', category: code, stage,
      remedy: REMEDIES[code] || '请查看环境检查和授权状态；保留本机配置后反馈此状态文件。' }, null, 2));
    throw fail(code);
  } finally {
    await context?.close().catch(() => {});
    await home?.release();
  }
}
async function main(argv = process.argv.slice(2)) {
  try {
    const result = await run(argumentsFor([...argv]));
    process.stdout.write(JSON.stringify(result) + '\n');
  } catch (error) {
    process.stderr.write(JSON.stringify({ status: 'failed', category: error.code || 'INVALID_ARGUMENT' }) + '\n');
    process.exitCode = 2;
  }
}
module.exports = { argumentsFor, run, main };
if (require.main === module) main();
