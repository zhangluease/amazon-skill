const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { check } = require('../skills/amazon-jp-competitor-tracking/scripts/diagnostics.cjs');
const { sessionFromBridge } = require('../skills/amazon-jp-competitor-tracking/scripts/sellersprite_auth.cjs');

const manifest = { version: '5.0.7', key: Buffer.from('fixture-key').toString('base64') };
const session = { format: 'sellersprite-http-session-v1', extension_version: '5.0.7',
  headers: { 'auth-token': 'fixture-private-token', 'auth-fp': 'fixture-fp', 'random-token': 'fixture-random' } };
const dependencies = {
  findPython: () => ({ command: 'test-python', prefix: [] }),
  inspectRuntime: async () => ({ playwright_installed: false, browser_installed: false, npm_available: true }),
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

test('corrupt and placeholder credentials cannot be treated as a valid bridge session', () => {
  for (const value of ['null', 'undefined', '***', '[redacted]', 'token\tcontrol', 'token\x7f']) {
    assert.equal(sessionFromBridge({ ...session, headers: { ...session.headers, 'auth-token': value } }, '5.0.7'), null);
  }
  assert.equal(sessionFromBridge({ ...session, extension_version: undefined }, undefined), null);
});
