// Browser dependencies are prepared on demand for authorization or Amazon fallback.
const fs = require('node:fs/promises');
const syncFs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createRequire } = require('node:module');
const { spawnSync, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const execute = promisify(execFile);
const VERSION = require('../package.json').dependencies.playwright;
const ROOT = path.resolve(__dirname, '..');
const CACHE = path.join(os.homedir(), '.amazon-skill', 'cache', 'competitor-runtime', `playwright-${VERSION}`);

function findPython() {
  const options = process.env.AMAZON_COMPETITOR_PYTHON
    ? [[process.env.AMAZON_COMPETITOR_PYTHON, []]] : [['python3', []], ['python', []], ['py', ['-3']]];
  for (const [command, prefix] of options) {
    const r = spawnSync(command, [...prefix, '-c', 'import sys;sys.exit(0 if sys.version_info >= (3,9) else 1)'],
      { timeout: 5000, windowsHide: true });
    if (r.status === 0) return { command, prefix };
  }
  throw Object.assign(Error('PYTHON_39_REQUIRED'), { code: 'PYTHON_39_REQUIRED' });
}

function desktopAvailability({ env = process.env, platform = process.platform } = {}) {
  if (env.AMAZON_COMPETITOR_INTERACTIVE === '0') return { available: false, reason: 'disabled' };
  if (env.AMAZON_COMPETITOR_INTERACTIVE === '1') return { available: true, reason: 'explicit_desktop' };
  if (env.SSH_CONNECTION || env.SSH_TTY || env.CI && !/^(0|false)$/i.test(env.CI)
    || syncFs.existsSync('/.dockerenv') || syncFs.existsSync('/run/.containerenv'))
    return { available: false, reason: 'remote_or_container' };
  if (platform === 'darwin' || platform === 'win32' && !/^services$/i.test(env.SESSIONNAME || ''))
    return { available: true, reason: 'local_desktop' };
  return { available: Boolean((env.DISPLAY || env.WAYLAND_DISPLAY) && (env.XDG_CURRENT_DESKTOP || env.DESKTOP_SESSION)),
    reason: 'desktop_session_required' };
}

function runtime() {
  for (const root of [ROOT, CACHE]) {
    try {
      const load = createRequire(path.join(root, 'package.json'));
      const file = load.resolve('playwright/package.json');
      if (load('playwright/package.json').version === VERSION) return { playwright: load('playwright'), file };
    } catch { /* Use the private cache next. */ }
  }
  return null;
}

function npmCommand() {
  const bases = [path.dirname(process.execPath), ...String(process.env.PATH || '').split(path.delimiter)];
  for (const base of bases) {
    for (const file of [path.join(base, 'node_modules/npm/bin/npm-cli.js'),
      path.resolve(base, '../lib/node_modules/npm/bin/npm-cli.js')]) {
      if (syncFs.existsSync(file)) return [process.execPath, [file]];
    }
  }
  return process.platform === 'win32' ? null : ['npm', []];
}

async function inspectRuntime(python) {
  let settings;
  try {
    const config = await execute(python.command, [...python.prefix, path.join(__dirname, 'amazon_config.py'), 'browser-status'],
      { timeout: 5000, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    settings = JSON.parse(config.stdout);
  } catch { throw Object.assign(Error('BROWSER_CONFIG_INVALID'), { code: 'BROWSER_CONFIG_INVALID' }); }
  process.env.PLAYWRIGHT_BROWSERS_PATH = settings.cache_dir;
  const found = runtime();
  const npm = npmCommand();
  const npmAvailable = Boolean(npm && spawnSync(npm[0], [...npm[1], '--version'],
    { timeout: 5000, windowsHide: true, stdio: 'ignore' }).status === 0);
  return { playwright_installed: Boolean(found), required_playwright_version: VERSION,
    browser_installed: Boolean(found && syncFs.existsSync(found.playwright.chromium.executablePath())),
    npm_available: npmAvailable, browser_cache: settings.cache_dir };
}

async function ensureEnvironment({ out, prepare = false } = {}) {
  if (Number(process.versions.node.split('.')[0]) < 18) throw Object.assign(Error('NODE_18_REQUIRED'), { code: 'NODE_18_REQUIRED' });
  const python = findPython();
  let settings;
  try {
    const config = await execute(python.command, [...python.prefix, path.join(__dirname, 'amazon_config.py'), 'browser-status'],
      { timeout: 5000, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    settings = JSON.parse(config.stdout);
  } catch { throw Object.assign(Error('BROWSER_CONFIG_INVALID'), { code: 'BROWSER_CONFIG_INVALID' }); }
  process.env.PLAYWRIGHT_BROWSERS_PATH = settings.cache_dir;
  let found = runtime();
  if (!found && prepare) {
    const npm = npmCommand();
    if (!npm) throw Object.assign(Error('NPM_REQUIRED'), { code: 'NPM_REQUIRED' });
    await fs.mkdir(CACHE, { recursive: true });
    await fs.writeFile(path.join(CACHE, 'package.json'), JSON.stringify({ private: true, dependencies: { playwright: VERSION } }));
    try {
      await execute(npm[0], [...npm[1], 'install', '--prefix', CACHE, '--ignore-scripts', '--no-audit', '--no-fund'],
        { timeout: 120000, windowsHide: true, maxBuffer: 1024 * 1024 });
    } catch { throw Object.assign(Error('PLAYWRIGHT_INSTALL_FAILED'), { code: 'PLAYWRIGHT_INSTALL_FAILED' }); }
    found = runtime();
  }
  if (!found) throw Object.assign(Error('PLAYWRIGHT_MISSING'), { code: 'PLAYWRIGHT_MISSING' });
  const launch = () => found.playwright.chromium.launch({ channel: 'chromium', headless: true, timeout: 15000 });
  let browser;
  try { browser = await launch(); }
  catch (error) {
    if (!prepare || !/executable.*(?:exist|found)/i.test(String(error.message)))
      throw Object.assign(Error('BROWSER_LAUNCH_FAILED'), { code: 'BROWSER_LAUNCH_FAILED' });
    try {
      await execute(process.execPath, [path.join(path.dirname(found.file), 'cli.js'), 'install', 'chromium'],
        { timeout: 180000, maxBuffer: 1024 * 1024, windowsHide: true });
    } catch { throw Object.assign(Error('BROWSER_INSTALL_FAILED'), { code: 'BROWSER_INSTALL_FAILED' }); }
    try { browser = await launch(); }
    catch { throw Object.assign(Error('BROWSER_LAUNCH_FAILED'), { code: 'BROWSER_LAUNCH_FAILED' }); }
  }
  let version;
  try { version = browser.version(); } finally { await browser.close(); }
  const report = { ready: true, browser_version: version, playwright_version: VERSION,
    browser_cache: settings.cache_dir, collection_browser_required: false };
  if (out) {
    await fs.mkdir(out, { recursive: true });
    await fs.writeFile(path.join(out, 'environment.json'), JSON.stringify(report, null, 2));
  }
  return { report, playwright: found.playwright };
}

module.exports = { findPython, ensureEnvironment, desktopAvailability, inspectRuntime };
