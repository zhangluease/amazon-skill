const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { cacheRoot, findPython, findPlaywright, outputCheck, launchAvailable, parseArguments }
  = require('../skills/pod-image-selection/scripts/environment.cjs');

test('finds a supported Python instead of accepting an older first interpreter', () => {
  const calls = [];
  const result = findPython((command, args) => {
    calls.push([command, args]);
    return { status: 0, stdout: JSON.stringify({ version: command === 'python3' ? [3, 8, 9] : [3, 11, 2], executable: command }) };
  }, {});
  assert.equal(result.command, 'python');
  assert.equal(result.version, '3.11.2');
  assert.equal(calls.length, 2);
});

test('reports missing Python and respects an explicitly selected interpreter', () => {
  const calls = [];
  const result = findPython((command) => { calls.push(command); return { status: null }; },
    { POD_HUABAN_PYTHON: '/custom/python' });
  assert.equal(result.missing, true);
  assert.deepEqual(calls, ['/custom/python']);
});

test('version mismatch uses the isolated pinned runtime without changing the first install', async () => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-version-'));
  try {
    for (const [name, version] of [['other', '0.0.1'], ['pinned', '1.61.0']]) {
      const folder = path.join(temporary, name, 'node_modules', 'playwright');
      await fs.mkdir(folder, { recursive: true });
      await fs.writeFile(path.join(folder, 'package.json'), JSON.stringify({ name: 'playwright', version, main: 'index.js' }));
      await fs.writeFile(path.join(folder, 'index.js'), 'module.exports={chromium:{launch(){}}};');
    }
    const runtime = findPlaywright([path.join(temporary, 'other'), path.join(temporary, 'pinned')]);
    assert.equal(runtime.version, '1.61.0');
    assert.equal(runtime.rejected[0].version, '0.0.1');
    const original = JSON.parse(await fs.readFile(path.join(temporary, 'other/node_modules/playwright/package.json')));
    assert.equal(original.version, '0.0.1');
  } finally { await fs.rm(temporary, { recursive: true, force: true }); }
});

test('missing Chrome falls back to managed Chromium and verifies it can render', async () => {
  const calls = [], browser = { async newPage() { return {
    async setContent() {}, async title() { return 'huaban-environment-check'; }, async close() {}
  }; }, async close() {} };
  const runtime = { chromium: { async launch(options) {
    calls.push(options);
    if (options.channel === 'chrome') throw Error('Chrome distribution not found');
    return browser;
  } } };
  const result = await launchAvailable(runtime);
  assert.equal(result.engine, 'chromium');
  assert.equal(result.browser, browser);
  assert.equal(calls.length, 2);
  assert.equal(result.attempts[0].engine, 'chrome');
  await result.browser.close();
});

test('a file cannot be used as an output directory and write probes clean up', async () => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-output-'));
  try {
    const conflict = path.join(temporary, 'file'); await fs.writeFile(conflict, 'keep');
    assert.equal((await outputCheck(conflict)).status, 'error');
    const output = path.join(temporary, 'reports');
    assert.equal((await outputCheck(output)).status, 'ok');
    assert.deepEqual(await fs.readdir(output), []);
    assert.equal(await fs.readFile(conflict, 'utf8'), 'keep');
  } finally { await fs.rm(temporary, { recursive: true, force: true }); }
});

test('runtime cache stays outside the skill and smoke is deliberately bounded', () => {
  const root = cacheRoot({ POD_HUABAN_CACHE_DIR: '/tmp/huaban-private-cache' }, 'linux', '/unused');
  assert.equal(root, path.resolve('/tmp/huaban-private-cache/playwright-1.61.0'));
  assert.equal(parseArguments(['smoke', '--limit', '3']).limit, 3);
  assert.throws(() => parseArguments(['smoke', '--limit', '1000']));
  assert.throws(() => parseArguments(['check', '--browser', 'unknown']));
});
