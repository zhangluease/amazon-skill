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
    verify: async () => ({ status: 'session_verified' }),
    authorize: auth || (async () => assert.fail('A valid session must not open a browser')), notify: () => {} };
}

async function options(tmp) {
  return argumentsFor(['collect', '--out', path.join(tmp, 'out'), '--state-dir', path.join(tmp, 'state'),
    '--session-file', path.join(tmp, 'provided-session.json'), 'B008H0Y15I']);
}

test('amazon-offer validates input and never enters SellerSprite authorization', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-amazon-only-'));
  try {
    const calls = [];
    const dependencies = deps(async (_, argv) => {
      calls.push(argv);
      assert.ok(argv.includes('validate-input'));
      return { stdout: JSON.stringify(valid) };
    });
    dependencies.collectAmazon = async options => {
      assert.equal(options.asin, valid.asins[0]);
      assert.equal(options.mode, 'headless');
      return { status: 'ok', data: { asin: options.asin, price: 5387 } };
    };
    const result = await run(argumentsFor(['amazon-offer', valid.asins[0], '--out', path.join(tmp, 'out'),
      '--state-dir', path.join(tmp, 'state')]), dependencies);
    assert.equal(result.data.price, 5387);
    assert.equal(calls.length, 1);
    assert.equal(JSON.parse(await fs.readFile(path.join(tmp, 'out/amazon-offer.json'))).data.price, 5387);
    await assert.rejects(fs.access(path.join(tmp, 'state/.running')));
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

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

test('API-only renewal remains automatic and does not open a QR browser', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-api-login-'));
  try {
    const result = await run(await options(tmp), deps(async (command, argv) => ({ stdout: JSON.stringify(
      argv.includes('validate-input') ? valid : { status: 'partial', usable_products: 1,
        api_authorization: { events: [{ action: 'token_signin', reason: 'missing_snapshot_data', status: 'ok' }] } })
    })));
    assert.equal(result.authorization_handoffs, 0);
    assert.equal(result.api_authorization.events[0].status, 'ok');
    assert.equal(result.api_authorization.credentials_included, false);
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('failed authorization records the API cause and renewal attempt without credentials', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-auth-cause-'));
  try {
    const config = await options(tmp);
    const dependencies = deps(async (_, argv) => ({ stdout: JSON.stringify(argv.includes('validate-input') ? valid : {
      status: 'authorization_required', category: 'ERR_NEED_RE_AUTHORIZED', api_authorization: { events: [
        { action: 'token_signin', reason: 'ERR_NEED_RE_AUTHORIZED', status: 'failed', code: 'ERR_ACCOUNT_NOT_EXIST' },
      ] },
    }) }), async (_, __, publish) => {
      await publish('awaiting_login');
      throw Object.assign(new Error('AUTHORIZATION_TIMEOUT'), { code: 'AUTHORIZATION_TIMEOUT' });
    });
    await assert.rejects(run(config, dependencies), { code: 'AUTHORIZATION_TIMEOUT' });
    const status = JSON.parse(await fs.readFile(path.join(config.out, 'status.json')));
    const auth = JSON.parse(await fs.readFile(path.join(config.out, 'authorization.json')));
    assert.equal(status.authorization_reason, 'ERR_NEED_RE_AUTHORIZED');
    assert.equal(status.api_authorization.events[0].status, 'failed');
    assert.equal(status.api_authorization.credentials_included, false);
    assert.equal(auth.reason, 'ERR_NEED_RE_AUTHORIZED');
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('a login mask remaining after API renewal enters one browser login then resumes', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-masked-'));
  let queries = 0, logins = 0;
  const calls = [];
  try {
    const result = await run(await options(tmp), deps(async (command, argv) => {
      if (argv.includes('validate-input')) return { stdout: JSON.stringify(valid) };
      calls.push(argv);
      if (++queries === 1) throw { stderr: JSON.stringify({ status: 'authorization_required',
        category: 'PLUGIN_LOGIN_REQUIRED', api_authorization: { events: [
          { action: 'token_signin', reason: 'PLUGIN_LOGIN_REQUIRED', status: 'ok' }] } }) };
      return { stdout: JSON.stringify({ status: 'ok', usable_products: 1 }) };
    }, async () => { logins++; }));
    assert.equal(logins, 1);
    assert.equal(result.status, 'ok');
    assert.ok(calls[1].includes('--resume'));
    assert.equal(result.api_authorization.events[0].reason, 'PLUGIN_LOGIN_REQUIRED');
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('robot verification preserves partial results without automatic CAPTCHA or login retries', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-verify-'));
  const statuses = [];
  try {
    const dependencies = deps(async (command, argv) => {
      if (argv.includes('validate-input')) return { stdout: JSON.stringify(valid) };
      throw { stderr: JSON.stringify({ status: 'verification_required', category: 'ERR_ROBOT_CHECK', usable_products: 1 }) };
    });
    dependencies.notify = value => statuses.push(value.status);
    const result = await run(await options(tmp), dependencies);
    assert.equal(result.status, 'verification_required');
    assert.equal(result.usable_products, 1);
    assert.equal(result.authorization_handoffs, 0);
    assert.deepEqual(statuses, ['requires_user_verification']);
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
      return { stdout: JSON.stringify({ status: 'ok', usable_products: 1, media_changed_products: 1 }) };
    }, async () => { auths++; }));
    assert.equal(result.media_changed_products, 1);
    assert.equal(auths, 1);
    assert.ok(calls.every(argv => argv.includes('--all-history')));
    assert.ok(calls.slice(1).every(argv => argv.includes('changes') && !argv.includes('collect')));
    assert.ok(calls[2].includes('--resume'));
    assert.throws(() => argumentsFor(['collect', '--all-history', '--out', 'unused', 'B0CDCD694T']),
      { code: 'INVALID_ARGUMENT' });
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('interactive recovery opens the Japanese listing and waits for the original plugin login', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-qr-'));
  let home;
  try {
    const source = path.join(tmp, 'program');
    await fs.mkdir(source);
    await fs.writeFile(path.join(source, 'manifest.json'), JSON.stringify({ manifest_version: 3, version: '5.0.7',
      host_permissions: ['https://e.sellersprite.com/*'], action: { default_popup: 'popup.html' }, key: Buffer.from('fixture-key').toString('base64') }));
    await fs.writeFile(path.join(source, 'popup.html'), '<title>fixture</title>');
    home = await prepareHome(path.join(tmp, 'state'), path.join(tmp, 'out'));
    let launches = 0, closes = 0;
    const routeAppliedInHeadless = [];
    const statuses = [];
    const session = { format: 'sellersprite-http-session-v1', extension_version: '5.0.7',
      headers: { 'auth-token': 'fixture-token', 'auth-fp': 'fixture-fp', 'random-token': 'fixture-random' },
      parameters: { version: '5.0.7' } };
    await authorize(home, { out: path.join(tmp, 'out'), 'extension-dir': source, timeout: 5,
      authorizationAsin: 'B09BQ4KNTG' },
      async (status, details) => {
        statuses.push(status);
        if (status === 'awaiting_login') assert.equal(details.login_url, 'https://www.amazon.co.jp/dp/B09BQ4KNTG');
      }, {
        prepare: async () => ({ report: { ready: true } }), desktop: () => ({ available: true }),
        launch: async (profile, settings) => {
          launches++;
          let pages = 0;
          return { route: async () => routeAppliedInHeadless.push(settings.headless), close: async () => { closes++; }, newPage: async () => {
            if (++pages === 1) return { goto: async () => {}, isClosed: () => false,
              evaluate: async (_, allowWebsiteSync) => {
                assert.equal(allowWebsiteSync, settings.headless);
                if (settings.headless) return { plugin_ready: false, rejected: true, reason: 'ERR_ACCOUNT_NOT_EXIST' };
                assert.deepEqual(statuses, ['awaiting_login']);
                return { plugin_ready: true, session };
              } };
            return { goto: async url => assert.equal(url, 'https://www.amazon.co.jp/dp/B09BQ4KNTG'),
              bringToFront: async () => {}, isClosed: () => false };
          } };
        }
      });
    assert.equal(launches, 2);
    assert.equal(closes, 2);
    assert.deepEqual(routeAppliedInHeadless, [true]);
    assert.deepEqual(statuses, ['awaiting_login', 'completed']);
    const saved = JSON.parse(await fs.readFile(path.join(home.directory, 'http-session.json'), 'utf8'));
    assert.equal(saved.headers['auth-token'], 'fixture-token');
    if (process.platform !== 'win32') assert.equal((await fs.stat(path.join(home.directory, 'http-session.json'))).mode & 0o777, 0o600);
  } finally { await home?.release(); await fs.rm(tmp, { recursive: true, force: true }); }
});

test('successful login is followed by an API check before collection resumes', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-api-proof-'));
  const order = [];
  let queries = 0;
  try {
    const d = deps(async (_cmd, argv) => {
      if (argv.includes('validate-input')) return { stdout: JSON.stringify(valid) };
      if (argv.includes('verify-session')) {
        order.push('verify-api');
        assert.ok(!argv.includes(valid.asins[0]));
        return { stdout: JSON.stringify({ status: 'session_verified' }) };
      }
      order.push('query');
      return { stdout: JSON.stringify(++queries === 1 ? { status: 'authorization_required' } : { status: 'ok' }) };
    }, async () => { order.push('login'); });
    delete d.verify; // Exercise the production Python subcommand rather than a test shortcut.
    await run(await options(tmp), d);
    assert.deepEqual(order, ['query', 'login', 'verify-api', 'query']);
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});

test('login without API verification never resumes collection or changes to browser collection', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'competitor-proof-rejected-'));
  let queries = 0;
  try {
    const d = deps(async (_cmd, argv) => {
      if (argv.includes('validate-input')) return { stdout: JSON.stringify(valid) };
      queries++;
      return { stdout: JSON.stringify({ status: 'authorization_required', usable_products: 1 }) };
    }, async () => {});
    d.verify = async () => ({ status: 'verification_required', category: 'ERR_ROBOT_CHECK' });
    const result = await run(await options(tmp), d);
    assert.equal(queries, 1);
    assert.equal(result.status, 'verification_required');
    assert.equal(result.usable_products, 1);
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
});
