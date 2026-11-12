// All website responses below are local fixtures, including the simulated human action.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { findPlaywright } = require('../skills/pod-huaban-materials/scripts/environment.cjs');
const { collect } = require('../skills/pod-huaban-materials/scripts/huaban_browser.cjs');
const runtime = findPlaywright();
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=', 'base64');

test('collector resumes after a human handoff in the visible context and still caches image responses',
  { skip: !runtime.playwright, timeout: 30000 }, async t => {
    const output = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-handoff-integration-'));
    const browsers = [], pages = [], events = [], requests = [], actions = [];
    const actualHeaded = process.env.POD_HUABAN_TEST_HEADED === '1';
    let manualAction;
    t.after(async () => {
      await Promise.allSettled(browsers.map(browser => browser.close()));
      await fs.rm(output, { recursive: true, force: true });
    });
    async function launch(headless) {
      const browser = await runtime.playwright.chromium.launch({ headless });
      browsers.push(browser);
      const newContext = browser.newContext.bind(browser);
      browser.newContext = async options => {
        const context = await newContext(options);
        await context.route('**/*', async route => {
          const url = new URL(route.request().url());
          requests.push(url.pathname);
          const cookies = await context.cookies('https://huaban.com');
          if (url.hostname === 'img.huaban.com')
            return route.fulfill({ status: 200, contentType: 'image/png', body: png });
          if (url.hostname !== 'huaban.com') return route.abort();
          if (url.pathname === '/discovery') {
            await context.addCookies([{ name: 'fixture_seed', value: 'must-survive', domain: 'huaban.com', path: '/' }]);
            return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<title>花瓣</title><input placeholder="搜索">' });
          }
          if (url.pathname === '/search') {
            if (!cookies.some(cookie => cookie.name === 'fixture_passed'))
              return route.fulfill({ status: 403, contentType: 'text/html; charset=utf-8',
                body: '<title>安全验证 - EdgeOne</title><h1>确认您是真人</h1><button>模拟人工完成</button>' });
            return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<title>狗 动漫 - 花瓣</title><input placeholder="搜索">'
              + [1, 2, 3].map(id => `<a href="/pins/${id}"><img alt="狗 ${id}" src="https://img.huaban.com/fixture_key.png"></a>`).join('')
              + '<script>fetch("/v3/search/file");</script>' });
          }
          if (url.pathname === '/v3/search/file') return route.fulfill({ contentType: 'application/json',
            body: JSON.stringify({ pins: [1, 2, 3].map(pin_id => ({ pin_id,
              file: { key: 'fixture_key', width: 500, height: 500 } })) }) });
          return route.fulfill({ status: 404, body: '' });
        });
        const newPage = context.newPage.bind(context);
        context.newPage = async () => {
          const page = await newPage();
          pages.push(page);
          return page;
        };
        return context;
      };
      return browser;
    }
    const initial = await launch(true);
    const summary = await collect({ query: '狗 动漫', similar: [], board: [], limit: 3, out: output,
      quiet: true, 'verification-timeout': 10 }, {
      prepare: async () => ({ report: { ready: true, checks: { browser: { engine: 'chromium' } } },
        browser: initial, playwright: { chromium: { async launch(options) {
          assert.equal(options.headless, false);
          actions.push('requested_visible_browser');
          return launch(!actualHeaded);
        } } } }),
      desktop: () => ({ available: true, reason: 'local_fixture' }),
      notify(event) {
        events.push(event.status);
        if (event.status === 'awaiting_human') manualAction = (async () => {
          const page = pages.at(-1);
          const context = page.context();
          assert.equal((await context.cookies('https://huaban.com')).find(c => c.name === 'fixture_seed').value, 'must-survive');
          await page.getByRole('button', { name: '模拟人工完成' }).click();
          await context.addCookies([{ name: 'fixture_passed', value: 'yes', domain: 'huaban.com', path: '/' }]);
          await page.reload({ waitUntil: 'load' });
        })();
      }
    });
    await manualAction;
    assert.equal(summary.headless, false);
    assert.equal(summary.human_verification.status, 'completed');
    assert.equal(summary.candidates, 3);
    assert.equal(summary.cached, 3);
    assert.deepEqual(actions, ['requested_visible_browser']);
    assert.ok(events.indexOf('awaiting_human') < events.indexOf('completed'));
    assert.equal(requests.filter(value => value === '/search').length, 3); // Initial, visible, fixture human reload.
    assert.ok(browsers.every(browser => !browser.isConnected()));
    const pins = JSON.parse(await fs.readFile(path.join(output, 'response.json'))).pins;
    assert.equal(pins.length, 3);
    assert.ok(pins.every(pin => pin.source_paths[0].query === '狗 动漫'));
    assert.ok((await fs.readdir(path.join(output, 'browser-assets'))).length > 0);
    console.log(JSON.stringify({ fixture: 'human_handoff', actual_headed_launch: actualHeaded,
      candidates: summary.candidates, cached: summary.cached, events }));
  });

test('collector reports remote verification separately and closes its browser without launching a popup',
  { skip: !runtime.playwright, timeout: 15000 }, async t => {
    const output = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-remote-integration-'));
    const browser = await runtime.playwright.chromium.launch({ headless: true });
    t.after(async () => { await browser.close(); await fs.rm(output, { recursive: true, force: true }); });
    const newContext = browser.newContext.bind(browser);
    browser.newContext = async options => {
      const context = await newContext(options);
      await context.route('**/*', route => route.fulfill({ status: 403, contentType: 'text/html; charset=utf-8',
        body: '<title>安全验证 - EdgeOne</title><h1>确认您是真人</h1>' }));
      return context;
    };
    await assert.rejects(() => collect({ query: '狗 动漫', similar: [], board: [], limit: 3, out: output, quiet: true }, {
      prepare: async () => ({ report: { ready: true, checks: { browser: { engine: 'chromium' } } }, browser,
        playwright: { chromium: { async launch() { assert.fail('Remote must not launch an inaccessible popup'); } } } }),
      desktop: () => ({ available: false, reason: 'virtual_display' }), notify() {}
    }), { code: 'HUMAN_VERIFICATION_REQUIRED' });
    assert.equal(browser.isConnected(), false);
    const failure = JSON.parse(await fs.readFile(path.join(output, 'failure.json')));
    assert.equal(failure.category, 'human_verification_required');
    assert.equal(failure.verification.desktop.reason, 'virtual_display');
    assert.equal(await fs.stat(path.join(output, 'verification.jpg')).then(s => s.size > 0), true);
  });
