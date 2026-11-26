// Only local fixture responses are used; no account or live upload is involved.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { findPlaywright } = require('../skills/pod-image-selection/scripts/environment.cjs');
const { collect } = require('../skills/pod-image-selection/scripts/huaban_browser.cjs');
const runtime = findPlaywright();
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=', 'base64');
const largePng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAAD0lEQVR4nGP4z8DA8J8BAAf/Af8Bf4mnAAAAAElFTkSuQmCC', 'base64');

async function fixture(t, loggedIn) {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-image-integration-'));
  const browser = await runtime.playwright.chromium.launch({ headless: true });
  t.after(async () => { await browser.close(); await fs.rm(tmp, { recursive: true, force: true }); });
  const state = path.join(tmp, 'empty-state.json'), image = path.join(tmp, 'reference.png');
  await fs.writeFile(state, JSON.stringify({ cookies: [], origins: [] }));
  await fs.writeFile(image, png);
  const requests = [], uploads = [];
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async options => {
    const context = await newContext(options);
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      requests.push(url.pathname);
      if (url.hostname === 'img.huaban.com')
        return route.fulfill({ contentType: 'image/png', body: url.pathname.startsWith('/viewer/') ? largePng : png });
      if (url.hostname !== 'huaban.com') return route.abort();
      if (url.pathname === '/discovery') return route.fulfill({ contentType: 'text/html; charset=utf-8', body:
        '<title>花瓣</title><input aria-label="搜索花瓣内容">'
        + (loggedIn ? '' : '<button>登录/注册</button>')
        + '<button aria-label="以图搜索" onclick="document.getElementById(\'picker\').hidden=false">以图搜索</button>'
        + '<input id="picker" hidden type="file" accept="image/png" onchange="upload(this.files[0])">'
        + '<a href="/pins/999"><img src="https://img.huaban.com/unrelated.png"></a>'
        + '<script>async function upload(file){await fetch("/fixture/upload",{method:"POST",body:file});'
        + 'location.href="/similar?bucket=fixture&key=uploaded-reference&width=1&height=1";}</script>' });
      if (url.pathname === '/fixture/upload') {
        uploads.push(request.postDataBuffer());
        return route.fulfill({ contentType: 'application/json', body: '{}' });
      }
      if (url.pathname === '/similar') return route.fulfill({ contentType: 'text/html; charset=utf-8', body:
        '<title>以图搜图 - 花瓣</title><input aria-label="搜索花瓣内容">'
        + [11, 12, 13].map(id => `<a href="/pins/${id}"><img alt="image result ${id}" src="https://img.huaban.com/result${id}.png"></a>`).join('')
        + '<script>fetch("/v3/search/file?text=unrelated");</script>' });
      if (url.pathname === '/v3/search/file') return route.fulfill({ contentType: 'application/json',
        body: JSON.stringify({ pins: [{ pin_id: 888, file: { key: 'unrelated' } }] }) });
      if (['/pins/11', '/pins/12'].includes(url.pathname)) return route.fulfill({ contentType: 'text/html; charset=utf-8', body:
        `<title>花瓣采集</title><img src="https://img.huaban.com/main-thumbnail.png">
        <button onclick="document.querySelector('#imageViewerWrapper').hidden=false; document.querySelector('#imageViewerWrapper img').src='https://img.huaban.com/viewer${url.pathname}.png?signature=keep'">查看大图</button>
        <div id="imageViewerWrapper" hidden><img alt="查看图片"></div>
        <button onclick="location.href='/fixture/eps'">下载 EPS</button>` });
      if (url.pathname === '/pins/13') return route.fulfill({ status: 404, body: '' });
      return route.fulfill({ status: 404, body: '' });
    });
    return context;
  };
  return { browser, uploads, requests, options: { image, state, 'source-asin': 'B000000001',
    similar: [], board: [], limit: 3, quiet: true, out: path.join(tmp, 'out') }, dependencies: {
    prepare: async () => ({ report: { ready: true, checks: { browser: { engine: 'chromium' } } },
      browser, playwright: runtime.playwright }),
    notify() {}, rateLimitDirectory: path.join(tmp, 'rate-limit')
  } };
}

test('confirmed image-search results use exact detail viewer files and never fall back to thumbnails',
  { skip: !runtime.playwright, timeout: 30000 }, async t => {
    const f = await fixture(t, true);
    const summary = await collect(f.options, f.dependencies);
    assert.equal(summary.search_mode, 'image_search');
    assert.equal(summary.candidates, 3);
    assert.equal(summary.cached, 2);
    assert.equal(summary.large_image_unavailable, 1);
    assert.equal(summary.image_search_rate_limit.waited_ms, 0);
    assert.ok(summary.image_search_rate_limit.cooldown_ms >= 4000);
    assert.ok(summary.image_search_rate_limit.cooldown_ms <= 6000);
    assert.deepEqual(f.uploads, [png]);
    const pins = JSON.parse(await fs.readFile(path.join(f.options.out, 'response.json'))).pins;
    assert.deepEqual(pins.map(pin => pin.pin_id), ['11', '12', '13']);
    assert.equal(f.requests.includes('/fixture/eps'), false);
    for (const pin of pins) {
      assert.equal(pin.source_paths[0].via, 'image_search');
      assert.equal(pin.source_paths[0].source_asin, 'B000000001');
      assert.match(pin.source_paths[0].reference_sha256, /^[a-f0-9]{64}$/);
      assert.ok(pin.source_paths[0].search_url.includes('/similar?bucket=fixture'));
      if (pin.pin_id === '13') {
        assert.equal(pin.large_image_status, 'unavailable');
        assert.equal(pin.image_url, null);
        assert.equal(pin.local_image, undefined);
      } else {
        assert.deepEqual([pin.large_image_width, pin.large_image_height], [2, 1]);
        assert.equal(pin.image_url, `https://img.huaban.com/viewer/pins/${pin.pin_id}.png?signature=keep`);
        assert.deepEqual(await fs.readFile(pin.local_image), largePng);
      }
    }
    assert.equal(f.browser.isConnected(), false);
    const rate = JSON.parse(await fs.readFile(path.join(f.dependencies.rateLimitDirectory, 'huaban-image-search-rate.json')));
    assert.equal(rate.next_allowed_at_ms - rate.completed_at_ms, summary.image_search_rate_limit.cooldown_ms);
    await assert.rejects(fs.access(path.join(f.dependencies.rateLimitDirectory, 'huaban-image-search.lock')));
  });

test('anonymous upload stops with a login requirement without returning discovery recommendations',
  { skip: !runtime.playwright, timeout: 20000 }, async t => {
    const f = await fixture(t, false);
    await assert.rejects(() => collect(f.options, f.dependencies), { code: 'IMAGE_SEARCH_LOGIN_REQUIRED' });
    assert.equal(f.uploads.length, 0);
    assert.equal(f.requests.includes('/similar'), false);
    await assert.rejects(fs.access(path.join(f.options.out, 'response.json')));
    const failure = JSON.parse(await fs.readFile(path.join(f.options.out, 'failure.json')));
    assert.equal(failure.category, 'image_search_login_required');
    assert.ok(failure.remedy.includes('--state'));
    assert.equal(f.browser.isConnected(), false);
    await assert.rejects(fs.access(path.join(f.dependencies.rateLimitDirectory, 'huaban-image-search.lock')));
  });
