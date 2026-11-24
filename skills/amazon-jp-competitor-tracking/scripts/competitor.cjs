#!/usr/bin/env node
// Only authorization uses a browser. All product/history requests run in Python HTTP.
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { findPython, ensureEnvironment } = require('./environment.cjs');
const { stateRoot, prepareHome, fail } = require('./sellersprite_session.cjs');
const { authorize, sessionFromBridge, saveSession } = require('./sellersprite_auth.cjs');
const { check, guidance } = require('./diagnostics.cjs');
const execute = promisify(execFile);
const HELP = `日本亚马逊竞对页面追踪
依赖：Node.js 18+、Python 3.9+；首次登录还需本机卖家精灵原扩展和可见桌面。

  node competitor.cjs check
  node competitor.cjs prepare --out 新目录
  node competitor.cjs changes ASIN --out 新目录
  node competitor.cjs changes ASIN --all-history --out 新目录
  node competitor.cjs collect ASIN --out 新目录
  node competitor.cjs login --out 新目录

changes 只查图片/视频修改图标，默认近七个日本自然日期；collect 还采集商品、销量与报价。
支持 --input 清单、--as-of 日期、--interval 1、--state-dir 专用会话目录、--extension-dir 扩展程序目录。
会话有效时直接调用 HTTP；鉴权失效或商品数据缺失时先尝试一次 API 续登，必要时最多扫码一次。每轮使用新的输出目录。
`;

function argumentsFor(argv) {
  if (argv.some(arg => ['--help', '-h'].includes(arg)) || argv[0] === 'help') return { command: 'help' };
  const args = [...argv];
  const command = args[0] && !args[0].startsWith('--') ? args.shift() : 'collect';
  if (!['collect', 'changes', 'login', 'check', 'prepare', 'validate-input'].includes(command)) throw fail('INVALID_COMMAND');
  const options = { command, asins: [], timeout: 300, interval: 1 };
  for (let i = 0; i < args.length; i++) {
    if (!args[i].startsWith('--')) { options.asins.push(args[i]); continue; }
    const key = args[i].slice(2);
    if (key === 'all-history' && command === 'changes') { options.allHistory = true; continue; }
    if (!['out', 'input', 'as-of', 'state-dir', 'session-file', 'extension-dir', 'authorization-timeout', 'interval'].includes(key)
      || !args[i + 1] || args[i + 1].startsWith('--')) throw fail('INVALID_ARGUMENT');
    options[key] = args[++i];
  }
  options.timeout = Number(options['authorization-timeout'] || 300);
  options.interval = Number(options.interval);
  if (!Number.isInteger(options.timeout) || options.timeout < 5 || options.timeout > 900) throw fail('INVALID_TIMEOUT');
  if (!Number.isFinite(options.interval) || options.interval < 1 || options.interval > 60) throw fail('INVALID_INTERVAL');
  if (!['check', 'validate-input'].includes(command) && !options.out) throw fail('MISSING_OUTPUT');
  if (['collect', 'changes', 'validate-input'].includes(command) && !options.input && !options.asins.length) throw fail('MISSING_ASINS');
  return options;
}

function errorCode(error) {
  return typeof error.code === 'string' && /^[A-Z0-9_]{1,80}$/.test(error.code) ? error.code : 'COLLECTION_FAILED';
}

function legacySession() {
  const home = os.homedir();
  const directory = process.env.POD_IMAGE_SELECTION_STATE_DIR || (process.platform === 'darwin'
    ? path.join(home, 'Library', 'Application Support', 'pod-image-selection')
    : process.platform === 'win32' ? path.join(process.env.LOCALAPPDATA || path.join(home, 'AppData/Local'), 'pod-image-selection')
      : path.join(process.env.XDG_DATA_HOME || path.join(home, '.local/share'), 'pod-image-selection'));
  return path.join(directory, 'http-session.json');
}

async function run(options, dependencies = {}) {
  if (options.command === 'help') return { status: 'help', usage: HELP };
  const invoke = dependencies.execute || execute;
  let python;
  const inputArgs = [...(options.input ? ['--input', path.resolve(options.input)] : []),
    ...(options['as-of'] ? ['--as-of', options['as-of']] : []),
    ...(options.allHistory ? ['--all-history'] : []), ...options.asins];
  const call = async (command, extra = []) => {
    try {
      const r = await invoke(python.command, [...python.prefix, path.join(__dirname, 'competitor_client.py'), command,
        ...extra, ...inputArgs], { timeout: ['collect', 'changes'].includes(command) ? (validated?.asins.length || 1) * 235000 + 30000 : 30000,
        windowsHide: true, maxBuffer: 1024 * 1024,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
      return JSON.parse(r.stdout);
    } catch (error) {
      let r;
      try { r = JSON.parse(error.stdout || error.stderr); } catch { throw fail('HTTP_WORKER_FAILED'); }
      if (['authorization_required', 'verification_required'].includes(r.status)) return r;
      throw fail(r.category || 'HTTP_WORKER_FAILED');
    }
  };
  const ownSession = path.join(options['state-dir'] || stateRoot(), 'http-session.json');
  let session = options['session-file'] || ownSession;
  if (!options['session-file'] && !await fs.stat(session).then(s => s.isFile()).catch(() => false)) {
    const old = legacySession();
    if (await fs.stat(old).then(s => s.isFile()).catch(() => false)) session = old;
  }
  if (options.command === 'check') {
    return (dependencies.check || check)(options, session, dependencies.diagnostics || {});
  }
  if (Number(process.versions.node.split('.')[0]) < 18) throw fail('NODE_18_REQUIRED');
  python = (dependencies.findPython || findPython)();
  // Validate the entire input before preparing a login or opening a browser.
  let validated;
  if (['collect', 'changes', 'validate-input'].includes(options.command)) {
    validated = await call('validate-input');
    if (options.command === 'validate-input') return validated;
    // Keep the exact same date window if an expired session requires a retry.
    if (!options['as-of']) inputArgs.unshift('--as-of', validated.window.end);
  }
  const output = path.resolve(options.out);
  await fs.mkdir(output, { recursive: true });
  const resultFile = options.command === 'changes' ? 'changes.json' : 'products.json';
  if (['collect', 'changes'].includes(options.command)) {
    for (const name of ['products.json', 'changes.json', 'report.html']) {
      if (await fs.stat(path.join(output, name)).then(() => true).catch(() => false))
        throw fail('OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY');
    }
  }
  let home, handoffs = 0;
  const publish = async (status, details = {}) => {
    const record = { status, ...details };
    await fs.writeFile(path.join(output, 'authorization.json'), JSON.stringify(record, null, 2));
    (dependencies.notify || (value => process.stderr.write(JSON.stringify(value) + '\n')))({ event: 'seller_authorization', ...record });
  };
  const login = async () => {
    if (++handoffs > 1) throw fail('AUTHORIZATION_REPEATED');
    await (dependencies.authorize || authorize)(home, { ...options, out: output }, publish, dependencies.auth || {});
    session = path.join(home.directory, 'http-session.json');
  };
  try {
    home = await (dependencies.prepareHome || prepareHome)(options['state-dir'] || stateRoot(), output);
    if (options.command === 'prepare') {
      const env = await (dependencies.ensureEnvironment || ensureEnvironment)({ out: path.join(output, 'environment'), prepare: true });
      return env.report;
    }
    if (options.command === 'login') {
      await login();
      return { status: 'authorized', session_saved: true, api_tested: false };
    }
    // Copy only an already authorized, known managed cache; never read browser login databases.
    if (session !== path.join(home.directory, 'http-session.json') && !options['session-file']) {
      try {
        const value = JSON.parse(await fs.readFile(session, 'utf8'));
        const safe = sessionFromBridge(value, value.extension_version);
        if (safe) session = await saveSession(home, safe);
      } catch { /* The HTTP worker will classify a missing/invalid session. */ }
    }
    const apiAuthEvents = [];
    const query = async (resume = false) => {
      const value = await call(options.command, ['--out', output, '--session-file', session,
        '--interval', String(options.interval), ...(resume ? ['--resume'] : [])]);
      apiAuthEvents.push(...(value.api_authorization?.events || []));
      return value;
    };
    let result = await query();
    if (result.status === 'authorization_required') {
      await login();
      result = await query(true);
    } else if (result.status === 'verification_required') {
      await publish('requires_user_verification', { reason: result.category });
    } else await publish('not_required', { reason: 'http_session_valid_or_non_auth_api_error' });
    const summary = { ...result, authorization_handoffs: handoffs,
      ...(apiAuthEvents.length ? { api_authorization: { events: apiAuthEvents, credentials_included: false } } : {}),
      ...(result.category ? guidance(result.category) : {}) };
    await fs.writeFile(path.join(output, 'status.json'), JSON.stringify(summary, null, 2));
    return summary;
  } catch (error) {
    await fs.writeFile(path.join(output, 'status.json'), JSON.stringify({ status: 'failed', category: errorCode(error),
      ...guidance(errorCode(error)),
      partial_results: await fs.stat(path.join(output, resultFile)).then(() => true).catch(() => false) }, null, 2));
    throw error;
  } finally { await home?.release(); }
}

async function main(argv = process.argv.slice(2)) {
  try {
    const result = await run(argumentsFor(argv));
    process.stdout.write(result.status === 'help' ? result.usage : JSON.stringify(result) + '\n');
    if (['authorization_required', 'verification_required', 'failed', 'needs_setup'].includes(result.status) || result.usable_products === 0) process.exitCode = 2;
  } catch (error) {
    process.stderr.write(JSON.stringify({ status: 'failed', category: errorCode(error), ...guidance(errorCode(error)) }) + '\n');
    process.exitCode = 2;
  }
}

module.exports = { argumentsFor, run, main };
if (require.main === module) main();
