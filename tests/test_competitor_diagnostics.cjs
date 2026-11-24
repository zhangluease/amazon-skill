const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { check, prepareSetup } = require('../skills/amazon-jp-competitor-tracking/scripts/diagnostics.cjs');
const { argumentsFor } = require('../skills/amazon-jp-competitor-tracking/scripts/competitor.cjs');
const { sessionFromBridge } = require('../skills/amazon-jp-competitor-tracking/scripts/sellersprite_auth.cjs');

const manifest = { version: '5.0.7', key: Buffer.from('fixture-key').toString('base64') };
const session = { format: 'sellersprite-http-session-v1', extension_version: '5.0.7',
  headers: { 'auth-token': 'fixture-private-token', 'auth-fp': 'fixture-fp', 'random-token': 'fixture-random' } };
const dependencies = {
  findPython: () => ({ command: 'test-python', prefix: [] }),
  inspectRuntime: async () => ({ playwright_installed: false, browser_installed: false, npm_available: true }),
  inspectFeishu: async () => ({ ready: true }),
  discoverExtension: async () => ({ manifest }), readManifest: async () => null,
  desktopAvailability: () => ({ available: true }),
};

test('new user without extension receives setup instructions without changing state', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-doctor-'));
  try {
    const state = path.join(tmp, 'state');
    const report = await check({ 'state-dir': state }, path.join(state, 'http-session.json'),
      { ...dependencies, discoverExtension: async () => null });
    assert.equal(report.status, 'needs_setup');
    assert.equal(report.ready_for_http, false);
    assert.ok(report.issues.some(issue => issue.code === 'EXTENSION_NOT_FOUND' && issue.next_step));
    await assert.rejects(fs.access(state));
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('login dependency preparation is distinct from a ready browser and authenticated HTTP', async () => {
  const report = await check({ 'state-dir': '/nonexistent-fixture-state' }, '/nonexistent-fixture-session', dependencies);
  assert.equal(report.status, 'ready');
  assert.equal(report.login_can_prepare, true);
  assert.equal(report.login_preparation_required, true);
  assert.equal(report.ready_for_login, false);
  assert.equal(report.ready_for_http, false);
  assert.equal(report.session.authenticated, 'not_tested');
});

test('valid cached HTTP can run without login dependencies and no headers reach diagnostics', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-cached-'));
  try {
    const file = path.join(tmp, 'http-session.json');
    await fs.writeFile(file, JSON.stringify(session));
    const report = await check({ 'state-dir': tmp }, file,
      { ...dependencies, discoverExtension: async () => null });
    assert.equal(report.ready_for_http, true);
    assert.equal(report.status, 'ready');
    assert.ok(report.warnings.some(issue => issue.code === 'EXTENSION_NOT_FOUND'));
    assert.equal(JSON.stringify(report).includes('fixture-private-token'), false);
    assert.equal(JSON.stringify(report).includes('auth-token'), false);
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('missing Python is reported together with other setup issues', async () => {
  const report = await check({ 'state-dir': '/nonexistent-fixture-state' }, '/nonexistent-fixture-session',
    { ...dependencies, findPython: () => { throw Error('missing'); } });
  assert.equal(report.status, 'needs_setup');
  assert.equal(report.python_found, false);
  assert.ok(report.issues.some(issue => issue.code === 'PYTHON_39_REQUIRED'));
});

test('missing Feishu CLI is optional for ASIN-only use and required with --feishu', async () => {
  const probes = { ...dependencies, inspectFeishu: async () => ({ ready: false, category: 'FEISHU_CLI_REQUIRED' }) };
  const local = await check(argumentsFor(['check']), '/nonexistent-fixture-session', probes);
  assert.equal(local.status, 'ready');
  assert.equal(local.feishu.required, false);
  assert.ok(local.warnings.some(issue => issue.code === 'FEISHU_CLI_REQUIRED'));
  const feishu = await check(argumentsFor(['check', '--feishu']), '/nonexistent-fixture-session', probes);
  assert.equal(feishu.status, 'needs_setup');
  assert.equal(feishu.feishu.required, true);
  assert.ok(feishu.issues.some(issue => issue.code === 'FEISHU_CLI_REQUIRED'));
});

test('preparing the extension reports actual load even when Feishu scopes are missing', async () => {
  const output = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-prepare-'));
  const home = {}, python = {}, playwright = {};
  const extension = { manifest, id: 'fixture-extension' };
  try {
    const report = await prepareSetup(argumentsFor(['prepare', '--feishu', '--out', output]), home, python, output, {
      ensureEnvironment: async options => {
        assert.equal(options.prepare, true);
        return { report: { browser_version: '149' }, playwright };
      },
      prepareExtension: async preparedHome => {
        assert.equal(preparedHome, home);
        return extension;
      },
      smokeExtension: async (runtime, plugin) => {
        assert.equal(runtime, playwright);
        assert.equal(plugin, extension);
      },
      prepareFeishu: async interpreter => {
        assert.equal(interpreter, python);
        return { ready: false, category: 'FEISHU_CLI_SCOPE_REQUIRED' };
      },
    });
    assert.equal(report.extension.loaded, true);
    assert.equal(report.extension.load_tested, true);
    assert.equal(report.status, 'needs_setup');
    assert.equal(report.issues[0].code, 'FEISHU_CLI_SCOPE_REQUIRED');
    assert.deepEqual(JSON.parse(await fs.readFile(path.join(output, 'environment/environment.json'))), report);
  } finally { await fs.rm(output, { recursive: true, force: true }); }
});

test('corrupt and placeholder credentials cannot be treated as a valid bridge session', () => {
  for (const value of ['null', 'undefined', '***', '[redacted]', 'token\tcontrol', 'token\x7f']) {
    assert.equal(sessionFromBridge({ ...session, headers: { ...session.headers, 'auth-token': value } }, '5.0.7'), null);
  }
  assert.equal(sessionFromBridge({ ...session, extension_version: undefined }, undefined), null);
});
