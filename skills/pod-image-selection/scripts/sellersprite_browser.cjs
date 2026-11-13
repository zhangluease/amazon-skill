#!/usr/bin/env node
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { findPython, ensureEnvironment } = require('./environment.cjs');
const { stateRoot, fail, prepareHome, prepareExtension } = require('./sellersprite_session.cjs');
const { authorize } = require('./sellersprite_auth.cjs');
const execute = promisify(execFile);
const AUTH_ERRORS = new Set(['MISSING_HTTP_SESSION', 'INVALID_HTTP_SESSION', 'ERR_NEED_AUTHORIZED',
  'ERR_NEED_RE_AUTHORIZED', 'ERROR_UNAUTHORIZED']);
const REMEDIES = {
  EXTENSION_NOT_FOUND: '请在本机Chrome/Edge安装卖家精灵原插件，或指定已安装插件程序目录。',
  INTERACTIVE_BROWSER_REQUIRED: '首次授权或会话失效需要用户可操作的桌面；普通有效会话查询不需要浏览器。',
  PROFILE_BUSY: '另一个任务正在使用专用配置，请等其结束。',
  AUTHORIZATION_TIMEOUT: '登录等待超时，专用配置保留；重新运行原关键词即可。',
  AUTHORIZATION_CANCELLED: '授权窗口已关闭；请保持窗口打开直到脚本提示会话已保存。',
  AUTHORIZATION_REJECTED: '卖家精灵拒绝网页账号同步，请核对官网账号和插件授权状态。',
  AUTHORIZATION_BRIDGE_UNAVAILABLE: '插件账号存在但未取得完整接口会话，请反馈状态并核对插件版本。',
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

async function run(options, dependencies = {}) {
  const output = path.resolve(options.out), started = Date.now();
  await fs.mkdir(output, { recursive: true });
  let home, stage = 'preflight', handoffs = 0;
  const publish = async (status, details = {}) => {
    const record = { status, ...details };
    await fs.writeFile(path.join(output, 'authorization.json'), JSON.stringify(record, null, 2));
    (dependencies.notify || (v => process.stderr.write(JSON.stringify(v) + '\n')))({ event: 'seller_authorization', ...record });
  };
  try {
    const python = (dependencies.python || findPython)();
    if (!python) throw fail('PYTHON_NOT_FOUND');
    home = await prepareHome(options['state-dir'] || stateRoot(), output);
    const session = path.join(home.directory, 'http-session.json');
    if (['check', 'prepare'].includes(options.command)) {
      const extension = await prepareExtension(home, options['extension-dir'], dependencies.extension || {});
      const environment = await (dependencies.prepare || ensureEnvironment)({ out: path.join(output, 'environment'),
        prepare: options.command === 'prepare', browserPreference: 'chromium' });
      if (!environment.report.ready) throw fail('ENVIRONMENT_NOT_READY');
      const result = { status: 'ready', extension_version: extension.manifest.version, session_tested: false };
      await fs.writeFile(path.join(output, 'browser-status.json'), JSON.stringify(result, null, 2));
      return result;
    }
    const auth = async () => {
      stage = 'authorization'; handoffs++;
      if (handoffs > 1) throw fail('AUTHORIZATION_REPEATED');
      await (dependencies.authorize || authorize)(home, { ...options, out: output }, publish, dependencies.auth || {});
    };
    if (options.command === 'login') {
      await auth();
      const result = { status: 'authorized', session_saved: true };
      await fs.writeFile(path.join(output, 'browser-status.json'), JSON.stringify(result, null, 2));
      return result;
    }
    const query = async () => {
      stage = 'http_query';
      try {
        const result = await (dependencies.execute || execute)(python.command, [...python.prefix,
          path.join(__dirname, 'sellersprite_plugin_client.py'), 'search', '--query', options.query,
          '--session-file', session, '--out', output], { timeout: 35000, maxBuffer: 1024 * 1024 });
        return JSON.parse(result.stdout);
      } catch (error) {
        if (error.code === 3 && error.stdout) return JSON.parse(error.stdout);
        let category;
        try { category = JSON.parse(error.stderr).category; } catch {}
        throw fail(typeof category === 'string' && /^[A-Z0-9_]{1,80}$/.test(category) ? category : 'HTTP_QUERY_FAILED');
      }
    };
    let result;
    try { result = await query(); await publish('reused', { source: 'local_http_session' }); }
    catch (error) {
      if (!AUTH_ERRORS.has(error.code)) throw error;
      await auth();
      result = await query();
    }
    if (result.products) {
      stage = 'gallery';
      await (dependencies.execute || execute)(python.command, [...python.prefix,
        path.join(__dirname, 'render_product_gallery.py'), path.join(output, 'ranked-products.json')],
        { timeout: 30000, maxBuffer: 1024 * 1024 });
    }
    const summary = { ...result, collection_method: 'independent_http', authorization_handoffs: handoffs,
      elapsed_ms: Date.now() - started };
    await fs.writeFile(path.join(output, 'browser-status.json'), JSON.stringify(summary, null, 2));
    return summary;
  } catch (error) {
    const code = typeof error.code === 'string' && /^[A-Z0-9_]{1,80}$/.test(error.code) ? error.code : 'COLLECTION_FAILED';
    if (/^AUTHORIZATION_/.test(code)) await publish(code.replace('AUTHORIZATION_', '').toLowerCase());
    await fs.writeFile(path.join(output, 'browser-status.json'), JSON.stringify({ status: 'failed', category: code, stage,
      collection_method: 'independent_http', remedy: REMEDIES[code] || '请反馈此状态文件；不会改用Amazon页面或表格导出。' }, null, 2));
    throw fail(code);
  } finally { await home?.release(); }
}
async function main(argv = process.argv.slice(2)) {
  try { process.stdout.write(JSON.stringify(await run(argumentsFor([...argv]))) + '\n'); }
  catch (error) { process.stderr.write(JSON.stringify({ status: 'failed', category: error.code || 'INVALID_ARGUMENT' }) + '\n'); process.exitCode = 2; }
}
module.exports = { argumentsFor, run, main };
if (require.main === module) main();
