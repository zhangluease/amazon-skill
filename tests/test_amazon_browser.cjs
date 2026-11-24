const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { collectAmazon, parsePage } = require('../skills/amazon-jp-competitor-tracking/scripts/amazon_browser.cjs');
const { findPython } = require('../skills/amazon-jp-competitor-tracking/scripts/environment.cjs');
const ASIN = 'B0D9W5646D';

async function home() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'amazon-browser-'));
  await fs.writeFile(path.join(dir, '.amazon-jp-competitor-tracking'), '1');
  return dir;
}

test('public bridge parses empty a-offscreen without exposing raw page tokens', async () => {
  const html = `<input id="ASIN" value="${ASIN}"><span id="productTitle">Test</span>
    <input id="glowDestinationType" value="COUNTRY"><span id="glow-ingress-line2">日本</span>
    <div id="corePriceDisplay_desktop_feature_div"><span class="priceToPay"><span class="a-offscreen"></span>
    <span class="a-price-whole">5,387</span></span></div><span id="availability">现在有货。</span>
    <script>const secret = 'fixture-private-token';</script>`;
  const snapshot = await parsePage(findPython(), ASIN, html, null, false);
  assert.equal(snapshot.data.price, 5387);
  assert.equal(snapshot.data.stock, 'in_stock');
  assert.equal(snapshot.collection_method, 'browser_headless');
  assert.ok(!JSON.stringify(snapshot).includes('fixture-private-token'));
});

test('challenge closes the context, persists pause, and next ASIN does not launch', async () => {
  const directory = await home();
  let launched = 0, closed = 0;
  const dependencies = {
    ensureEnvironment: async () => ({}),
    launch: async () => {
      launched++;
      return { close: async () => { closed++; }, pages: () => [{
        goto: async () => ({ status: () => 200 }), evaluate: async () => 'AMAZON_CONTINUE_SHOPPING_REQUIRED',
      }] };
    },
  };
  try {
    const first = await collectAmazon({ asin: ASIN, stateDir: directory }, dependencies);
    const second = await collectAmazon({ asin: 'B008H0V15I', stateDir: directory }, dependencies);
    assert.equal(first.status, 'verification_required');
    assert.equal(first.category, 'AMAZON_CONTINUE_SHOPPING_REQUIRED');
    assert.equal(second.not_attempted, true);
    assert.ok(second.endpoint.endsWith('B008H0V15I'));
    assert.equal(launched, 1);
    assert.equal(closed, 1);
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test('headed recovery waits for user and clears shared pause only after verified product', async () => {
  const directory = await home();
  const events = [];
  let waits = 0, closed = 0;
  const snapshot = { status: 'ok', data: { asin: ASIN, price: 5387 }, delivery_destination: { country_code: 'JP', verified: true } };
  try {
    await fs.writeFile(path.join(directory, 'amazon-browser-status.json'), JSON.stringify({ status: 'verification_required' }));
    const result = await collectAmazon({ asin: ASIN, stateDir: directory, mode: 'headed' }, {
      ensureEnvironment: async () => ({}), notify: event => events.push(event), findPython: () => ({}),
      launch: async (_, options) => {
        assert.equal(options.headless, false);
        return { close: async () => { closed++; }, pages: () => [{
          goto: async () => ({ status: () => 200 }), evaluate: async () => 'AMAZON_CONTINUE_SHOPPING_REQUIRED',
          waitForFunction: async () => { waits++; }, url: () => 'https://www.amazon.co.jp/dp/' + ASIN,
          content: async () => '<public product>',
        }] };
      }, parsePage: async () => snapshot,
    });
    assert.equal(result.status, 'ok');
    assert.equal(waits, 1);
    assert.equal(closed, 1);
    assert.equal(events[0].status, 'waiting_for_user');
    assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'amazon-browser-status.json'))).status, 'ready');
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});
