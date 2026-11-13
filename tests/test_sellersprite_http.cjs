const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { run } = require('../skills/pod-image-selection/scripts/sellersprite_browser.cjs');

async function fixture(check) {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'pod-http-test-'));
  const options = { command: 'search', query: 'pet house', timeout: 5, out: path.join(tmp, 'out'), 'state-dir': path.join(tmp, 'state') };
  const calls = [];
  const dependencies = { python: () => ({ command: 'fixture-python', prefix: [] }), notify: () => {},
    prepare: () => { throw new Error('HTTP queries must not prepare a browser'); },
    authorize: () => { throw new Error('An active session must not open authorization'); },
    execute: async (command, args) => {
      calls.push(args);
      if (args[0].endsWith('amazon_search.py')) return { stdout: JSON.stringify({ status: 'ok', asins_found: 48, pages_loaded: 1 }) };
      if (args[0].endsWith('render_product_gallery.py')) return { stdout: '{}' };
      assert.ok(args.includes('--session-file'));
      assert.ok(args.includes('--asins-file'));
      assert.ok(!args.includes('--session-har'));
      return { stdout: JSON.stringify({ status: 'ok', products: 1, returned_candidates: 1 }) };
    } };
  try { await check(options, dependencies, calls); }
  finally { await fs.rm(tmp, { recursive: true, force: true }); }
}
test('valid HTTP sessions search and rank without preparing or opening a browser', async () => fixture(async (options, dependencies, calls) => {
  const result = await run(options, dependencies);
  assert.equal(result.collection_method, 'independent_http');
  assert.equal(result.authorization_handoffs, 0);
  assert.equal(calls.length, 3);
  assert.equal(result.amazon_asins_found, 48);
}));
test('only a missing/expired session authorizes once and resumes the identical keyword', async () => fixture(async (options, dependencies, calls) => {
  const execute = dependencies.execute;
  let queries = 0, authorizations = 0;
  dependencies.execute = async (command, args) => {
    if (args.includes('search') && !queries++) throw { stderr: '{"category":"MISSING_HTTP_SESSION"}' };
    return execute(command, args);
  };
  dependencies.authorize = async (home, opts, publish) => { authorizations++; await publish('completed', { session_saved: true }); };
  const result = await run(options, dependencies);
  assert.equal(result.authorization_handoffs, 1);
  assert.equal(authorizations, 1);
  assert.equal(queries, 2);
  const lookup = calls.find(args => args.includes('search'));
  assert.equal(lookup[lookup.indexOf('--query') + 1], 'pet house');
  assert.equal(calls.filter(args => args[0].endsWith('amazon_search.py')).length, 1);
}));
test('quota/network failures do not trigger a browser or a UI fallback', async () => fixture(async (options, dependencies) => {
  const execute = dependencies.execute;
  dependencies.execute = async (command, args) => {
    if (args.includes('search')) throw { stderr: '{"category":"HTTP_429"}' };
    return execute(command, args);
  };
  await assert.rejects(run(options, dependencies), { code: 'HTTP_429' });
  const result = JSON.parse(await fs.readFile(path.join(options.out, 'browser-status.json')));
  assert.equal(result.stage, 'http_query');
  assert.equal(result.collection_method, 'independent_http');
}));
test('rejected renewed sessions stop without repeated authorization', async () => fixture(async (options, dependencies) => {
  let count = 0;
  const execute = dependencies.execute;
  dependencies.execute = async (command, args) => {
    if (args.includes('search')) throw { stderr: '{"category":"ERR_NEED_RE_AUTHORIZED"}' };
    return execute(command, args);
  };
  dependencies.authorize = async () => { count++; };
  await assert.rejects(run(options, dependencies), { code: 'ERR_NEED_RE_AUTHORIZED' });
  assert.equal(count, 1);
}));
test('Amazon verification stops before SellerSprite authorization or any browser fallback', async () => fixture(async (options, dependencies) => {
  dependencies.execute = async () => { throw { stderr: '{"category":"AMAZON_HUMAN_VERIFICATION_REQUIRED"}' }; };
  await assert.rejects(run(options, dependencies), { code: 'AMAZON_HUMAN_VERIFICATION_REQUIRED' });
  const status = JSON.parse(await fs.readFile(path.join(options.out, 'browser-status.json')));
  assert.equal(status.stage, 'amazon_search');
}));
test('confirmed empty Amazon search never queries or authorizes SellerSprite', async () => fixture(async (options, dependencies) => {
  let count = 0;
  dependencies.execute = async (command, args) => {
    count++;
    assert.ok(args[0].endsWith('amazon_search.py'));
    return { stdout: '{"status":"empty","asins_found":0,"pages_loaded":1}' };
  };
  const result = await run(options, dependencies);
  assert.equal(result.status, 'empty');
  assert.equal(result.lookup_performed, false);
  assert.equal(count, 1);
}));
