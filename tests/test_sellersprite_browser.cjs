const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { prepareHome, prepareExtension, stateRoot, extensionId } = require('../skills/pod-image-selection/scripts/sellersprite_session.cjs');
const { sessionFromBridge, isAmazon } = require('../skills/pod-image-selection/scripts/sellersprite_auth.cjs');
const { argumentsFor } = require('../skills/pod-image-selection/scripts/sellersprite_browser.cjs');

test('only complete dedicated-profile sessions survive the private field whitelist', () => {
  const value = { format: 'sellersprite-http-session-v1', extension_version: '1.0.0',
    headers: { 'auth-token': 'new', 'auth-fp': 'fixture-fp', 'random-token': 'fixture-random', Cookie: 'excluded' },
    account: 'excluded', parameters: { version: '1.0.0', secret: 'excluded' } };
  const session = sessionFromBridge(value, '1.0.0');
  assert.equal(session.headers['auth-token'], 'new');
  assert.ok(!JSON.stringify(session).includes('excluded'));
  assert.equal(sessionFromBridge(value, '2.0.0'), null);
  assert.equal(sessionFromBridge({ ...value, headers: {} }, '1.0.0'), null);
  assert.equal(sessionFromBridge(null, '1.0.0'), null);
  assert.ok(isAmazon('https://www.amazon.com/'));
  assert.ok(isAmazon('https://sellercentral.amazon.co.jp/'));
  assert.ok(!isAmazon('https://www.sellersprite.com/'));
});
test('query and authorization bounds are validated', () => {
  assert.equal(argumentsFor(['search', '--query', ' pet house ', '--out', '/tmp/out']).query, 'pet house');
  assert.throws(() => argumentsFor(['search', '--out', '/tmp/out']));
  assert.throws(() => argumentsFor(['login', '--out', '/tmp/out', '--authorization-timeout', '9999']));
  assert.ok(stateRoot({}, 'linux', '/tmp/person').startsWith('/tmp/person/.local/share/'));
});
test('dedicated directory locks and refusal to use a repository/output as a profile', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'pod-session-test-'));
  try {
    const home = await prepareHome(path.join(tmp, 'state'), path.join(tmp, 'out'));
    await assert.rejects(prepareHome(home.directory, path.join(tmp, 'out')), { code: 'PROFILE_BUSY' });
    await home.release();
    const again = await prepareHome(home.directory, path.join(tmp, 'out')); await again.release();
    await assert.rejects(prepareHome(path.join(tmp, 'out', 'profile'), path.join(tmp, 'out')), { code: 'UNSAFE_STATE_DIRECTORY' });
    await fs.mkdir(path.join(tmp, 'repo', '.git'), { recursive: true });
    await assert.rejects(prepareHome(path.join(tmp, 'repo', 'state'), path.join(tmp, 'out')), { code: 'STATE_DIRECTORY_IN_REPOSITORY' });
    await fs.symlink(path.join(tmp, 'repo'), path.join(tmp, 'alias'), process.platform === 'win32' ? 'junction' : 'dir');
    await assert.rejects(prepareHome(path.join(tmp, 'alias', 'state'), path.join(tmp, 'out')), { code: 'STATE_DIRECTORY_IN_REPOSITORY' });
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});
test('only extension program files are copied and cached without account storage', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'pod-copy-test-'));
  let home;
  try {
    home = await prepareHome(path.join(tmp, 'state'), path.join(tmp, 'out'));
    const source = path.join(tmp, 'program'); await fs.mkdir(path.join(source, '_metadata'), { recursive: true });
    const manifest = { manifest_version: 3, version: '1.0.0', name: 'fixture', host_permissions: ['https://e.sellersprite.com/*'], action: { default_popup: 'popup.html' } };
    await fs.writeFile(path.join(source, 'manifest.json'), JSON.stringify(manifest));
    await fs.writeFile(path.join(source, 'popup.html'), '<title>fixture</title>');
    await fs.writeFile(path.join(source, '_metadata', 'verified_contents.json'), '{}');
    const first = await prepareExtension(home, source);
    assert.equal(first.manifest.version, '1.0.0');
    await assert.rejects(fs.access(path.join(home.extension, '_metadata')));
    const cached = await prepareExtension(home, null, { discover: async () => null });
    assert.equal(cached.manifest.version, '1.0.0');
    assert.equal(extensionId(manifest), null);
  } finally { await home?.release(); await fs.rm(tmp, { recursive: true, force: true }); }
});
