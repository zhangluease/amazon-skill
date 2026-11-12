#!/usr/bin/env node
// Local preflight and bounded dependency preparation. No login state is read.
const fs = require('node:fs/promises');
const syncFs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const { execFile, spawnSync } = require('node:child_process');
const { promisify } = require('node:util');
const execute = promisify(execFile);
const SKILL = path.resolve(__dirname, '..');
const VERSION = require('../package.json').dependencies.playwright;

function cacheRoot(env = process.env, platform = process.platform, home = os.homedir()) {
  const base = env.POD_HUABAN_CACHE_DIR || (platform === 'win32'
    ? path.join(env.LOCALAPPDATA || path.join(home, 'AppData', 'Local'), 'pod-huaban-materials')
    : platform === 'darwin' ? path.join(home, 'Library', 'Caches', 'pod-huaban-materials')
    : path.join(env.XDG_CACHE_HOME || path.join(home, '.cache'), 'pod-huaban-materials'));
  return path.resolve(base, `playwright-${VERSION}`);
}

function pythonAtLeast39(version) {
  return Array.isArray(version) && (version[0] > 3 || version[0] === 3 && version[1] >= 9);
}

function findPython(run = spawnSync, env = process.env) {
  const candidates = env.POD_HUABAN_PYTHON
    ? [[env.POD_HUABAN_PYTHON, []]] : [['python3', []], ['python', []], ['py', ['-3']]];
  const versions = [];
  for (const [command, prefix] of candidates) {
    try {
      const result = run(command, [...prefix, '-c',
        'import json,sys;print(json.dumps({"version":list(sys.version_info[:3]),"executable":sys.executable}))'],
      { encoding: 'utf8', timeout: 5000, windowsHide: true });
      if (result.status !== 0) continue;
      const data = JSON.parse(result.stdout);
      versions.push(data.version.join('.'));
      if (pythonAtLeast39(data.version)) return { command, prefix, version: data.version.join('.'), executable: data.executable };
    } catch { /* Try the next standard interpreter, without echoing its output. */ }
  }
  return { versions, missing: versions.length === 0 };
}

function findNpm() {
  // Invoke npm's JS entrypoint through Node where possible, including Windows .cmd installations.
  const bases = [path.dirname(process.execPath), ...String(process.env.PATH || '').split(path.delimiter)
    .map(value => value.replace(/^"|"$/g, '')).filter(Boolean)];
  const candidates = [];
  try { candidates.push(path.join(path.dirname(require.resolve('npm/package.json')), 'bin', 'npm-cli.js')); } catch {}
  for (const base of bases) {
    candidates.push(path.join(base, 'node_modules', 'npm', 'bin', 'npm-cli.js'));
    candidates.push(path.resolve(base, '..', 'lib', 'node_modules', 'npm', 'bin', 'npm-cli.js'));
    candidates.push(path.resolve(base, '..', 'share', 'nodejs', 'npm', 'bin', 'npm-cli.js'));
    try {
      const real = syncFs.realpathSync(path.join(base, 'npm'));
      if (real.endsWith('npm-cli.js')) candidates.push(real);
    } catch {}
  }
  for (const candidate of [...new Set(candidates)]) {
    if (!syncFs.existsSync(candidate)) continue;
    const result = spawnSync(process.execPath, [candidate, '--version'], { encoding: 'utf8', timeout: 8000, windowsHide: true });
    if (result.status === 0 && /^\d+\.\d+/.test(result.stdout.trim()))
      return { command: process.execPath, prefix: [candidate], version: result.stdout.trim() };
  }
  if (process.platform !== 'win32') {
    const result = spawnSync('npm', ['--version'], { encoding: 'utf8', timeout: 8000 });
    if (result.status === 0 && /^\d+\.\d+/.test(result.stdout.trim()))
      return { command: 'npm', prefix: [], version: result.stdout.trim() };
  }
  return null;
}

function findPlaywright(roots = [SKILL, cacheRoot()]) {
  const rejected = [];
  for (const root of roots) {
    try {
      const loader = createRequire(path.join(path.resolve(root), 'package.json'));
      const packagePath = loader.resolve('playwright/package.json');
      const metadata = JSON.parse(syncFs.readFileSync(packagePath, 'utf8'));
      if (metadata.version !== VERSION) { rejected.push({ version: metadata.version, package_path: packagePath }); continue; }
      const playwright = loader('playwright');
      if (typeof playwright.chromium?.launch !== 'function') continue;
      return { playwright, version: metadata.version, packagePath, rejected };
    } catch { /* Missing or incomplete module; the private runtime is the next candidate. */ }
  }
  return { rejected };
}

async function outputCheck(output) {
  let temporary;
  try {
    await fs.mkdir(output, { recursive: true });
    temporary = await fs.mkdtemp(path.join(output, '.huaban-check-'));
    await fs.writeFile(path.join(temporary, 'write-test'), 'ok', { flag: 'wx' });
    return { status: 'ok', path: output };
  } catch {
    return { status: 'error', path: output, remedy: '选择当前用户可写的输出目录，检查空间和目录权限。' };
  } finally {
    if (temporary) await fs.rm(temporary, { recursive: true, force: true }).catch(() => {});
  }
}

async function launchAvailable(playwright, { preference = 'auto', headless = true } = {}) {
  const choices = preference === 'auto' ? ['chrome', 'chromium'] : [preference];
  const attempts = [];
  for (const engine of choices) {
    let browser;
    try {
      browser = await playwright.chromium.launch({ ...(engine === 'chrome' ? { channel: 'chrome' } : {}),
        headless, timeout: 12000 });
      const page = await browser.newPage();
      await page.setContent('<!doctype html><title>huaban-environment-check</title>');
      if (await page.title() !== 'huaban-environment-check') throw Error('Browser bridge failed');
      await page.close();
      return { browser, engine, attempts };
    } catch (error) {
      if (browser) await browser.close().catch(() => {});
      attempts.push({ engine, category: /executable.*(?:exist|found)|not found|distribution.*not found/i.test(String(error.message))
        ? 'browser_missing' : 'browser_launch_failed' });
    }
  }
  return { attempts };
}

async function installPlaywright(npm, root) {
  await fs.mkdir(root, { recursive: true });
  await fs.writeFile(path.join(root, 'package.json'), JSON.stringify({
    name: 'pod-huaban-private-runtime', private: true, dependencies: { playwright: VERSION }
  }, null, 2));
  await execute(npm.command, [...npm.prefix, 'install', '--prefix', root, '--no-audit', '--no-fund', '--ignore-scripts'],
    { timeout: 120000, maxBuffer: 2 * 1024 * 1024, windowsHide: true });
}

async function installChromium(runtime) {
  await execute(process.execPath, [path.join(path.dirname(runtime.packagePath), 'cli.js'), 'install', 'chromium'],
    { timeout: 180000, maxBuffer: 2 * 1024 * 1024, windowsHide: true });
}

async function ensureEnvironment(options = {}) {
  const output = path.resolve(options.out || path.join(process.cwd(), 'reports', 'pod-materials', 'environment-check'));
  const report = { ready: false, network_tested: false, platform: process.platform, architecture: process.arch,
    expected_playwright: VERSION, runtime_cache: cacheRoot(), output, checks: {}, actions: [] };
  const major = Number(process.versions.node.split('.')[0]);
  report.checks.node = { status: major >= 18 ? 'ok' : 'unsupported', version: process.versions.node,
    ...(major >= 18 ? {} : { remedy: '安装 Node.js 18+（建议当前 LTS），重新打开 Agent 后再检查。' }) };
  const python = findPython();
  report.checks.python = python.command ? { status: 'ok', ...python } : {
    status: python.missing ? 'missing' : 'unsupported', found_versions: python.versions,
    remedy: '安装 Python 3.9+；可用 POD_HUABAN_PYTHON 指定解释器的完整路径。' };
  const npm = findNpm();
  report.checks.npm = npm ? { status: 'ok', version: npm.version, required: false } : {
    status: 'missing', required: false, remedy: '需要准备依赖时，请修复 Node.js 附带的 npm。' };
  report.checks.output = await outputCheck(output);
  let runtime = findPlaywright();
  const basicsReady = ['node', 'python', 'output'].every(key => report.checks[key].status === 'ok');
  if (!runtime.playwright && options.prepare && basicsReady && npm) {
    try {
      process.stderr.write('正在为花瓣 skill 准备私有 Playwright 运行环境…\n');
      await installPlaywright(npm, report.runtime_cache);
      report.actions.push('installed_private_playwright');
      runtime = findPlaywright();
    } catch {
      report.checks.install = { status: 'error', category: 'dependency_install_failed',
        remedy: '检查 npm 软件源、代理或网络后重试；本次安装已停止，不会修改全局 npm 包。' };
    }
  }
  report.checks.playwright = runtime.playwright ? { status: 'ok', version: runtime.version, package_path: runtime.packagePath }
    : { status: runtime.rejected.length ? 'version_mismatch' : 'missing', expected: VERSION,
      rejected: runtime.rejected, remedy: '运行 environment.cjs prepare，按需安装到用户缓存目录。' };
  let launched;
  if (basicsReady && runtime.playwright) {
    launched = await launchAvailable(runtime.playwright, { preference: options.browserPreference || 'auto',
      headless: options.headless !== false });
    if (!launched.browser && options.prepare && options.browserPreference !== 'chrome') {
      try {
        process.stderr.write('正在准备 Playwright 管理的 Chromium 浏览器…\n');
        await installChromium(runtime);
        report.actions.push('installed_managed_chromium');
        launched = await launchAvailable(runtime.playwright, { preference: 'chromium', headless: options.headless !== false });
      } catch {
        report.checks.browser_install = { status: 'error', category: 'browser_install_failed',
          remedy: '检查浏览器下载网络或代理；Linux 也可能需要系统浏览器运行库。' };
      }
    }
    report.checks.browser = launched.browser ? { status: 'ok', engine: launched.engine,
      version: launched.browser.version(), launch_verified: true } : {
      status: 'error', attempts: launched.attempts,
      remedy: '运行 prepare；若仍无法启动，检查系统运行库、浏览器安装或企业执行策略。' };
  } else report.checks.browser = { status: 'skipped', remedy: '先解决 Node、Python、输出目录或 Playwright 问题。' };
  report.ready = ['node', 'python', 'output', 'playwright', 'browser'].every(key => report.checks[key].status === 'ok');
  report.report_path = path.join(output, 'environment.json');
  try { await fs.writeFile(report.report_path, JSON.stringify(report, null, 2) + '\n'); }
  catch { report.ready = false; report.checks.output.status = 'error'; }
  if (launched?.browser && (!options.keepBrowser || !report.ready)) await launched.browser.close();
  return { report, browser: report.ready && options.keepBrowser ? launched?.browser : null,
    playwright: runtime.playwright, python };
}

function parseArguments(argv) {
  const command = argv[0] && !argv[0].startsWith('--') ? argv.shift() : 'check';
  if (!['check', 'prepare', 'smoke'].includes(command)) throw Error('Unknown environment command');
  const options = { command, query: '狗 动漫', limit: 3 };
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i].replace(/^--/, ''), value = argv[++i];
    if (!['out', 'query', 'limit', 'browser'].includes(key) || !value) throw Error('Invalid environment option');
    options[key] = value;
  }
  options.limit = Number(options.limit);
  if (!Number.isInteger(options.limit) || options.limit < 1 || options.limit > 10) throw Error('Smoke limit must be 1–10');
  if (options.browser && !['auto', 'chrome', 'chromium'].includes(options.browser)) throw Error('Invalid browser');
  return options;
}

async function main(argv = process.argv.slice(2)) {
  const options = parseArguments([...argv]);
  if (options.command === 'smoke') {
    const out = path.resolve(options.out || path.join(process.cwd(), 'reports', 'pod-materials', 'environment-smoke'));
    try {
      const summary = await require('./huaban_browser.cjs').collect({ query: options.query, limit: options.limit,
        similar: [], board: [], out, quiet: true, browser: options.browser || 'auto' });
      const passed = summary.candidates > 0 && summary.cached === summary.candidates;
      const result = { ready: passed, network_tested: true, command: 'smoke', summary, output: out };
      await fs.writeFile(path.join(out, 'smoke.json'), JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result));
      return passed ? 0 : 3;
    } catch (error) {
      const result = { ready: false, network_tested: error.code !== 'ENVIRONMENT_NOT_READY', command: 'smoke',
        category: error.code === 'ENVIRONMENT_NOT_READY' ? 'environment_not_ready' : 'site_or_collection_failed', output: out };
      await fs.writeFile(path.join(out, 'smoke.json'), JSON.stringify(result, null, 2)).catch(() => {});
      console.log(JSON.stringify(result));
      return error.code === 'ENVIRONMENT_NOT_READY' ? 2 : 3;
    }
  }
  const { report } = await ensureEnvironment({ out: options.out, browserPreference: options.browser || 'auto',
    prepare: options.command === 'prepare' });
  console.log(JSON.stringify({ command: options.command, ...report }));
  return report.ready ? 0 : 2;
}

module.exports = { cacheRoot, pythonAtLeast39, findPython, findPlaywright, outputCheck, launchAvailable,
  ensureEnvironment, parseArguments, main };
if (require.main === module) main().then(code => { process.exitCode = code; }).catch(() => {
  console.error(JSON.stringify({ ready: false, category: 'environment_check_failed',
    remedy: '检查参数和输出目录；未输出任何 Cookie、token 或原始安装日志。' }));
  process.exitCode = 2;
});
