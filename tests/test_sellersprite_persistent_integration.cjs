const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { findPlaywright, findPython } = require('../skills/pod-image-selection/scripts/environment.cjs');
const { run } = require('../skills/pod-image-selection/scripts/sellersprite_browser.cjs');
const runtime = findPlaywright();

test('persistent extension UI: first authorization, reuse, expiry recovery and cancellation', { timeout: 90000 }, async () => {
  assert.ok(runtime.playwright, 'Prepare the pinned Playwright runtime before integration testing');
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'pod-persistent-test-'));
  const extension = path.join(tmp, 'fixture-extension');
  await fs.mkdir(extension);
  const key = crypto.generateKeyPairSync('rsa', { modulusLength: 1024 }).publicKey.export({ type: 'spki', format: 'der' }).toString('base64');
  await fs.writeFile(path.join(extension, 'manifest.json'), JSON.stringify({ manifest_version: 3, name: 'Seller UI fixture', version: '1.0.0', key,
    permissions: ['storage'], host_permissions: ['https://e.sellersprite.com/*'], action: { default_popup: 'popup.html' },
    content_scripts: [{ matches: ['https://www.amazon.com/*'], js: ['fixture.js'] }] }));
  await fs.writeFile(path.join(extension, 'popup.html'), '<title>fixture</title>');
  await fs.writeFile(path.join(extension, 'fixture.js'), `
    const root=document.createElement('div');root.id='main-sellersprite-extension';document.body.append(root);
    async function draw(){const state=await chrome.storage.local.get(['signedIn','expiredOnce']);
      if(!state.signedIn){root.innerHTML='<h1>卖家精灵用户登录</h1><button>模拟登录</button>';root.querySelector('button').onclick=async()=>{await chrome.storage.local.set({signedIn:true});draw()};return;}
      root.innerHTML='<a>产品查询</a><form name="header-search-form"><div class="product-search-selection"><div>ASIN</div><div class="product-search-select-item"><span>关键词</span></div></div><input placeholder="关键词 或 ASIN"><span class="input-group-suffix">搜索</span></form><div class="el-loading-mask" style="display:none">loading</div><div class="vxe-table--body-wrapper"><table><thead><tr><th>产品</th><th>近30天销量(父)</th><th>子体销量</th></tr></thead><tbody></tbody></table></div><div id="count"></div>';
      root.querySelector('.product-search-select-item').onclick=()=>root.querySelector('.product-search-selection>div').textContent='关键词';
      root.querySelector('.input-group-suffix').onclick=async()=>{const q=root.querySelector('input').value;
        if(q==='expire-once'&&!state.expiredOnce){await chrome.storage.local.set({signedIn:false,expiredOnce:true});draw();return;}
        if(q==='reject-query'){const error=document.createElement('div');error.className='el-message--error';error.textContent='查询失败';document.body.append(error);return;}
        root.querySelector('.el-loading-mask').style.display='block';setTimeout(()=>{
          const rows=Array.from({length:40},(_,i)=>'<tr><td><a href="https://www.amazon.com/dp/B'+String(i+1).padStart(9,'0')+'">'+q+'</a><img src="https://m.media-amazon.com/images/I/fixture.jpg"></td><td>'+(40-i)+'</td><td>99999</td></tr>').join('');
          root.querySelector('tbody').innerHTML=rows;root.querySelector('#count').textContent='加载数据40条';root.querySelector('.el-loading-mask').style.display='none';
        },100);
      };
    }draw();
  `);
  let current;
  const events = [];
  const dependencies = { prepare: async () => ({ report: { ready: true }, playwright: runtime.playwright, python: findPython() }),
    desktop: () => ({ available: true, reason: 'fixture' }),
    launch: async (profile, settings) => {
      // Headless fixture tests exercise the same persistent profile, not a real user's login.
      current = await runtime.playwright.chromium.launchPersistentContext(profile, { ...settings, headless: true });
      await current.route('https://www.amazon.com/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Fixture Amazon</title><body></body>' }));
      await current.route('https://m.media-amazon.com/**', route => route.abort());
      return current;
    },
    notify: value => { events.push(value); if(value.status==='awaiting_login') current.pages()[0].getByRole('button',{name:'模拟登录'}).click().catch(()=>{}); }
  };
  try {
    const common = { command: 'search', query: 'pet house', timeout: 5, 'extension-dir': extension, 'state-dir': path.join(tmp, 'state') };
    const first = await run({ ...common, headless: true, out: path.join(tmp, 'first') }, dependencies);
    assert.equal(first.products, 30); assert.equal(first.authorization_handoffs, 1);
    assert.equal(first.browser_headless, false);
    const data = JSON.parse(await fs.readFile(path.join(tmp, 'first', 'ranked-products.json')));
    assert.equal(data.source, 'sellersprite_extension_browser');
    assert.equal(data.products[0].parent_units_30d, 40);
    assert.equal(data.returned_candidates, 40);
    assert.ok(await fs.stat(path.join(tmp, 'first', 'products.html')));
    const second = await run({ ...common, out: path.join(tmp, 'second') }, dependencies);
    assert.equal(second.authorization_handoffs, 0);
    const expired = await run({ ...common, query: 'expire-once', out: path.join(tmp, 'expired') }, dependencies);
    assert.equal(expired.authorization_handoffs, 1); assert.equal(expired.products, 30);
    await assert.rejects(run({ ...common, query: 'reject-query', out: path.join(tmp, 'rejected') }, dependencies), { code: 'PLUGIN_QUERY_REJECTED' });
    const wrongMarket = { ...dependencies, launch: async (profile, settings) => {
      const browser = await dependencies.launch(profile, settings);
      await browser.route('https://www.amazon.com/**', route => route.fulfill({ status: 302, headers: { location: 'https://www.amazon.co.jp/' } }));
      await browser.route('https://www.amazon.co.jp/**', route => route.fulfill({ contentType: 'text/html', body: '<title>Wrong market</title>' }));
      return browser;
    } };
    await assert.rejects(run({ ...common, out: path.join(tmp, 'wrong-market') }, wrongMarket), { code: 'MARKETPLACE_NOT_US' });
    const cancel = { ...dependencies, notify: value => { if(value.status==='awaiting_login') current.close(); } };
    await assert.rejects(run({ ...common, 'state-dir': path.join(tmp, 'cancel-state'), out: path.join(tmp, 'cancel') }, cancel), { code: 'AUTHORIZATION_CANCELLED' });
    await assert.rejects(run({ ...common, timeout: 0.1, 'state-dir': path.join(tmp, 'timeout-state'), out: path.join(tmp, 'timeout') },
      { ...dependencies, notify: () => {} }), { code: 'AUTHORIZATION_TIMEOUT' });
    await assert.rejects(run({ ...common, 'state-dir': path.join(tmp, 'no-desktop'), out: path.join(tmp, 'no-desktop-out') },
      { ...dependencies, desktop: () => ({ available: false, reason: 'container' }) }), { code: 'INTERACTIVE_BROWSER_REQUIRED' });
    assert.ok(events.some(event => event.status === 'reused'));
    process.stdout.write(JSON.stringify({ fixturePersistentLogin: true, reuse: true, expiryRecovery: true,
      cancellation: true, timeout: true, noDesktop: true, realAccountAuthenticated: false })+'\n');
  } finally { await current?.close().catch(()=>{}); await fs.rm(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); }
});
