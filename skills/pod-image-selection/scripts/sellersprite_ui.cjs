const { setTimeout: delay } = require('node:timers/promises');
const { fail } = require('./sellersprite_session.cjs');

function parseSales(value) {
  const text = String(value || '').replace(/[，,\s]/g, '').trim();
  const m = text.match(/^(\d+(?:\.\d+)?)([kKmM万]?)$/);
  if (!m) return null;
  const n = Number(m[1]) * ({ k: 1000, m: 1000000, '万': 10000 }[m[2].toLowerCase()] || 1);
  return Number.isFinite(n) ? Math.round(n) : null;
}

class SellerSpriteUI {
  constructor(context, page) { this.context = context; this.page = page; }
  async open() {
    // A normal Amazon page provides the plugin's US marketplace context; no buyer login is needed.
    if (new URL(this.page.url() === 'about:blank' ? 'https://www.amazon.com/' : this.page.url()).hostname !== 'www.amazon.com')
      await this.page.goto('https://www.amazon.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    else if (this.page.url() === 'about:blank')
      await this.page.goto('https://www.amazon.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (new URL(this.page.url()).hostname !== 'www.amazon.com') throw fail('MARKETPLACE_NOT_US');
    this.root = this.page.locator('#main-sellersprite-extension');
    try { await this.root.waitFor({ state: 'attached', timeout: 15000 }); }
    catch {
      const text = await this.page.locator('body').innerText();
      if (/captcha|Robot Check|验证码|验证.{0,4}真人/i.test(text)) return;
      throw fail('PLUGIN_PANEL_NOT_FOUND');
    }
    const close = this.root.locator('.el-dialog__headerbtn:visible');
    if (await close.count()) await close.first().click().catch(() => {});
    const floating = this.root.getByText('卖家精灵', { exact: true });
    if (await floating.count() && await floating.first().isVisible()) await floating.first().click();
  }
  async authorization() {
    if (this.page.isClosed()) return 'cancelled';
    if (!await this.root.count()) {
      const text = await this.page.locator('body').innerText();
      return /captcha|Robot Check|验证码|验证.{0,4}真人/i.test(text) ? 'verification_required' : 'loading';
    }
    const login = this.root.getByText('卖家精灵用户登录', { exact: true });
    if (await login.isVisible().catch(() => false)) return 'login_required';
    const password = this.root.locator('input[type="password"]:visible');
    if (await password.count()) return 'login_required';
    const verify = this.root.getByText(/确认.{0,4}(?:您|你).{0,4}真人|请完成.{0,8}验证|人机验证/);
    if (await verify.count() && await verify.first().isVisible()) return 'verification_required';
    const product = this.root.getByText('产品查询', { exact: true });
    if (await product.count() && await product.first().isVisible()) return 'ready';
    const floating = this.root.getByText('卖家精灵', { exact: true });
    if (await floating.count() && await floating.first().isVisible()) await floating.first().click();
    return 'loading';
  }
  async query(keyword) {
    const product = this.root.getByText('产品查询', { exact: true });
    await product.first().click();
    // Select the plugin's own keyword mode. Avoid unrelated keyword-miner tools.
    const selection = this.root.locator('.product-search-selection:visible');
    if (await selection.count()) {
      await selection.first().hover();
      await selection.first().locator('.product-search-select-item').getByText('关键词', { exact: true }).click();
      if (!/关键词/.test(await selection.first().locator(':scope > div').first().innerText())) throw fail('KEYWORD_MODE_NOT_CONFIRMED');
    } else {
      const selector = this.root.locator('.el-select:visible').filter({ hasText: /^\s*(?:ASIN|关键词)\s*$/ });
      if (!await selector.count()) throw fail('KEYWORD_MODE_NOT_FOUND');
      await selector.first().click();
      const option = this.page.getByRole('option', { name: '关键词', exact: true });
      if (await option.count()) await option.last().click();
      else {
        const fallback = this.page.locator('.el-select-dropdown:visible').getByText('关键词', { exact: true });
        await fallback.last().click();
      }
    }
    const form = this.root.locator('form[name="header-search-form"]:visible');
    const field = form.locator('input:visible').first();
    await field.waitFor({ state: 'visible', timeout: 10000 });
    await field.fill(keyword);
    const previous = await this.rowSignature();
    const submit = form.locator('.input-group-suffix:visible');
    if (await submit.count()) await submit.click();
    else await field.press('Enter');
    // The existing rows must stop representing the previously loaded Amazon page.
    const deadline = Date.now() + 25000;
    let loadingObserved = false;
    while (Date.now() < deadline) {
      const state = await this.authorization();
      if (['login_required', 'verification_required'].includes(state)) throw fail('AUTHORIZATION_EXPIRED');
      if (state === 'cancelled') throw fail('AUTHORIZATION_CANCELLED');
      const errors = this.page.locator('.el-message--error:visible, .el-notification.error:visible');
      if (await errors.count()) {
        const message = await errors.first().innerText();
        if (/重新登录|请.{0,6}登录|授权.*(?:失效|过期)/.test(message)) throw fail('AUTHORIZATION_EXPIRED');
        throw fail('PLUGIN_QUERY_REJECTED');
      }
      const loading = await this.root.locator('.el-loading-mask:visible').count();
      loadingObserved ||= loading > 0;
      const signature = await this.rowSignature();
      if (!loading && (loadingObserved || signature && signature !== previous)) break;
      await delay(200);
    }
    if (Date.now() >= deadline) throw fail('QUERY_RESULT_NOT_CONFIRMED');
    return this.collect(keyword);
  }
  async rowSignature() {
    return this.root.locator('.vxe-body--row:visible, tbody tr:visible').evaluateAll(rows =>
      rows.slice(0, 3).map(r => r.innerText).join('|'));
  }
  async collect(keyword) {
    const rows = new Map(); let stationary = 0, previous = '';
    for (let turn = 0; turn < 50 && stationary < 3; turn++) {
      const current = await this.root.evaluate(root => {
        const text = node => (node?.innerText || '').replace(/\s+/g, ' ').trim();
        const headers = [...root.querySelectorAll('.vxe-header--column, thead th')].filter(h => h.getClientRects().length);
        const sales = headers.find(h => /销量/.test(text(h)) && !/子|销售额|增长|趋势/.test(text(h)));
        const column = sales?.getAttribute('colid') || sales?.getAttribute('data-colid');
        const headerIndex = sales ? [...sales.parentElement.children].indexOf(sales) : -1;
        const grouped = new Map();
        for (const row of root.querySelectorAll('.vxe-body--row, tbody tr')) {
          if (!row.getClientRects().length) continue;
          const key = row.getAttribute('rowid') || row.getAttribute('data-rowid') || text(row).slice(0, 100);
          const item = grouped.get(key) || {};
          const links = [...row.querySelectorAll('a[href]')];
          const link = links.find(a => /\/(?:dp|gp\/product)\/[A-Z0-9]{10}/.test(a.href));
          const asin = link?.href.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/)?.[1]
            || text(row).match(/\bB[A-Z0-9]{9}\b/)?.[0];
          if (asin) item.asin = asin;
          if (link) item.title = text(link) || link.title;
          const image = [...row.querySelectorAll('img')].find(i => /(?:media-amazon|ssl-images-amazon)\.com/.test(i.currentSrc || i.src));
          if (image) item.image = image.currentSrc || image.src;
          const cell = column ? row.querySelector(`[colid="${CSS.escape(column)}"], [data-colid="${CSS.escape(column)}"]`)
            : headerIndex >= 0 ? row.children[headerIndex] : null;
          if (cell) item.sales_display = text(cell);
          grouped.set(key, item);
        }
        const empty = [...root.querySelectorAll('.vxe-table--empty-content, .el-empty__description')]
          .some(n => n.getClientRects().length && /暂无数据|没有数据|No data/i.test(text(n)));
        const amount = text(root).match(/加载数据\s*([\d,]+)\s*条/) || text(root).match(/ASIN数量\s*([\d,]+)/);
        return { items: [...grouped.values()], salesColumn: Boolean(sales), empty,
          expected: amount ? Number(amount[1].replace(/,/g, '')) : null };
      });
      if (!current.salesColumn && !current.empty) throw fail('PARENT_SALES_COLUMN_NOT_FOUND');
      for (const item of current.items) if (item.asin) {
        const prior = rows.get(item.asin) || {};
        rows.set(item.asin, { ...prior, ...item, units: parseSales(item.sales_display ?? prior.sales_display) });
      }
      const signature = current.items.map(r => r.asin).join(',');
      stationary = signature === previous ? stationary + 1 : 0; previous = signature;
      if (current.empty && !rows.size) return { keyword, marketplace: 'US', items: [], explicit_empty: true };
      if (current.expected && rows.size >= current.expected) break;
      await this.root.locator('.vxe-table--body-wrapper').evaluateAll(nodes => nodes.forEach(node => { node.scrollTop += Math.max(200, node.clientHeight - 30); }));
      await delay(120);
    }
    if (!rows.size) throw fail('NO_CONFIRMED_PRODUCTS');
    const expected = await this.root.innerText().then(t => t.match(/加载数据\s*([\d,]+)\s*条/));
    if (expected && rows.size < Number(expected[1].replace(/,/g, ''))) throw fail('INCOMPLETE_TABLE_COLLECTION');
    return { keyword, marketplace: 'US', items: [...rows.values()], explicit_empty: false };
  }
}
module.exports = { parseSales, SellerSpriteUI };
