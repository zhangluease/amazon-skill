const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { argumentsFor, run } = require('../skills/amazon-jp-competitor-tracking/scripts/competitor.cjs');
const { authorize } = require('../skills/amazon-jp-competitor-tracking/scripts/sellersprite_auth.cjs');
const { prepareHome } = require('../skills/amazon-jp-competitor-tracking/scripts/sellersprite_session.cjs');

const valid = { status: 'valid', asins: ['B008H0Y15I'], window: { end: '2026-11-23T12:00:00+09:00' } };
function deps(worker, auth) {
  return { findPython: () => ({ command: 'fake-python', prefix: [] }), execute: worker,
    authorize: auth || (async () => assert.fail('A valid session must not open a browser')), notify: () => {} };
}

async function options(tmp) {
  return argumentsFor(['collect', '--out', path.join(tmp, 'out'), '--state-dir', path.join(tmp, 'state'),
    '--session-file', path.join(tmp, 'provided-session.json'), 'B008H0Y15I']);
}

test('valid cache queries through HTTP without browser preparation or login', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-valid-'));
  try {
    const calls = [];
    const result = await run(await options(tmp), deps(async (command, argv) => {
      calls.push(argv);
      return { stdout: JSON.stringify(argv.includes('validate-input') ? valid : { status: 'partial', usable_products: 1 }) };
    }));
    assert.equal(calls.length, 2);
    assert.equal(result.authorization_handoffs, 0);
    assert.ok(calls[1].includes('collect'));
    assert.ok(calls[1].includes(valid.window.end));
    await assert.rejects(fs.access(path.join(tmp, 'state', '.running')));
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('authorization runs once and resumes HTTP with a fixed window', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-auth-'));
  let auths = 0;
  const queries = [];
  try {
    const result = await run(await options(tmp), deps(async (command, argv) => {
      if (argv.includes('validate-input')) return { stdout: JSON.stringify(valid) };
      queries.push(argv);
      if (queries.length === 1) throw { code: 3, stdout: JSON.stringify({ status: 'authorization_required', category: 'ERR_NEED_RENEWAL_AUTHORIZED' }) };
      return { stdout: JSON.stringify({ status: 'partial', usable_products: 1 }) };
    }, async () => { auths++; }));
    assert.equal(auths, 1);
    assert.equal(result.authorization_handoffs, 1);
    assert.ok(queries[1].includes('--resume'));
    assert.ok(queries.every(argv => argv.includes(valid.window.end)));
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('repeated rejection does not open another login window', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-reject-'));
  let auths = 0;
  try {
    const result = await run(await options(tmp), deps(async (command, argv) => {
      if (argv.includes('validate-input')) return { stdout: JSON.stringify(valid) };
      throw { code: 3, stderr: JSON.stringify({ status: 'authorization_required', category: 'ERROR_UNAUTHORIZED' }) };
    }, async () => { auths++; }));
    assert.equal(result.status, 'authorization_required');
    assert.equal(auths, 1);
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('invalid ASIN fails validation before creating a profile or browser', async () => {
  let auths = 0;
  const options = argumentsFor(['collect', '--out', '/unused/output', 'BAD']);
  await assert.rejects(run(options, deps(async () => {
    throw { stderr: JSON.stringify({ status: 'failed', category: 'INVALID_JP_PRODUCT_URL_OR_ASIN' }) };
  }, async () => { auths++; })), { code: 'INVALID_JP_PRODUCT_URL_OR_ASIN' });
  assert.equal(auths, 0);
});

test('an existing report from another mode is rejected before collection or login', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-output-'));
  let calls = 0;
  try {
    const input = await options(tmp);
    await fs.mkdir(input.out);
    await fs.writeFile(path.join(input.out, 'changes.json'), '{"fixture":"preserve"}');
    await assert.rejects(run(input, deps(async () => {
      calls++;
      return { stdout: JSON.stringify(valid) };
    })), { code: 'OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY' });
    assert.equal(calls, 1); // local input validation only
    assert.equal(await fs.readFile(path.join(input.out, 'changes.json'), 'utf8'), '{"fixture":"preserve"}');
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('changes mode carries the all-period window through one login and resumes only that command', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-changes-'));
  const calls = [];
  let auths = 0;
  try {
    const input = argumentsFor(['changes', '--all-history', '--out', path.join(tmp, 'out'),
      '--state-dir', path.join(tmp, 'state'), 'B0CDCD694T']);
    const result = await run(input, deps(async (command, argv) => {
      calls.push(argv);
      if (argv.includes('validate-input')) return { stdout: JSON.stringify(valid) };
      if (calls.length === 2) throw { stdout: JSON.stringify({ status: 'authorization_required' }) };
      return { stdout: JSON.stringify({ status: 'ok', usable_products: 1, title_changes: 1, highlight_changes: 0 }) };
    }, async () => { auths++; }));
    assert.equal(result.title_changes, 1);
    assert.equal(auths, 1);
    assert.ok(calls.every(argv => argv.includes('--all-history')));
    assert.ok(calls.slice(1).every(argv => argv.includes('changes') && !argv.includes('collect')));
    assert.ok(calls[2].includes('--resume'));
    assert.throws(() => argumentsFor(['collect', '--all-history', '--out', 'unused', 'B0CDCD694T']),
      { code: 'INVALID_ARGUMENT' });
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('QR handoff saves the authorized session and closes only owned contexts', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-qr-'));
  let home;
  try {
    const source = path.join(tmp, 'program');
    await fs.mkdir(source);
    await fs.writeFile(path.join(source, 'manifest.json'), JSON.stringify({ manifest_version: 3, version: '5.0.7',
      host_permissions: ['https://e.sellersprite.com/*'], action: { default_popup: 'popup.html' }, key: Buffer.from('fixture-key').toString('base64') }));
    await fs.writeFile(path.join(source, 'popup.html'), '<title>fixture</title>');
    home = await prepareHome(path.join(tmp, 'state'), path.join(tmp, 'out'));
    let launches = 0, closes = 0, clicks = 0;
    const statuses = [];
    const session = { format: 'sellersprite-http-session-v1', extension_version: '5.0.7',
      headers: { 'auth-token': 'fixture-token', 'auth-fp': 'fixture-fp', 'random-token': 'fixture-random' },
      parameters: { version: '5.0.7' } };
    await authorize(home, { out: path.join(tmp, 'out'), 'extension-dir': source, timeout: 5 },
      async status => statuses.push(status), {
        prepare: async () => ({ report: { ready: true } }), desktop: () => ({ available: true }),
        launch: async (profile, settings) => {
          launches++;
          let pages = 0;
          return { route: async () => {}, close: async () => { closes++; }, newPage: async () => {
            if (++pages === 1) return { goto: async () => {}, isClosed: () => false,
              evaluate: async () => settings.headless ? { plugin_ready: false } : { plugin_ready: true, session } };
            return { goto: async url => assert.ok(url.endsWith('/w/user/login')), bringToFront: async () => {},
              isClosed: () => false, getByText: label => ({ first: () => ({
                isVisible: async () => label === '微信登录', click: async () => { clicks++; } }) }) };
          } };
        }
      });
    assert.equal(launches, 2);
    assert.equal(closes, 2);
    assert.equal(clicks, 1);
    assert.deepEqual(statuses, ['awaiting_login', 'completed']);
    const saved = JSON.parse(await fs.readFile(path.join(home.directory, 'http-session.json'), 'utf8'));
    assert.equal(saved.headers['auth-token'], 'fixture-token');
    if (process.platform !== 'win32') assert.equal((await fs.stat(path.join(home.directory, 'http-session.json'))).mode & 0o777, 0o600);
  } finally { await home?.release(); await fs.rm(tmp, { recursive: true, force: true }); }
});
