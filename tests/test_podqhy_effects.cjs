const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
let exportEffects, dimensions;
test.before(async () => { ({ exportEffects, dimensions } = await import('../skills/pod-image-selection/scripts/podqhy_effects.mjs')); });
const { findPlaywright } = require('../skills/pod-image-selection/scripts/environment.cjs');
const { closeOwnedBrowser } = require('../skills/pod-image-selection/scripts/browser_lifecycle.cjs');
const runtime = findPlaywright();

async function fixture(t, count, { overlay = false, identical = false } = {}) {
  const out = await fs.mkdtemp(path.join(os.tmpdir(), 'pod-effects-'));
  const browser = await runtime.playwright.chromium.launch({ headless: true });
  t.after(async () => { await closeOwnedBrowser(browser); await fs.rm(out, { recursive: true, force: true }); });
  const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
  const viewList = { data: Array.from({ length: count }, (_, i) => ({ id: 101 + i, name: '视角 ' + (i + 1) })) };
  const thumbnail = 'data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22/%3E';
  await page.setContent(`<style>body{margin:0}#editViewBox1{position:absolute;left:20px;top:20px}canvas{width:100px;height:100px}#editViewList1{position:absolute;left:500px;top:20px}#bad-ui{position:absolute;left:20px;top:20px;width:40px;height:40px;background:red}</style>
    <div id="editViewBox1"><canvas width="100" height="100"></canvas></div>
    <ul id="editFlatList1" style="display:none"><li data-fid="201" data-edit="1"><img src="${thumbnail}"></li></ul>
    ${overlay ? '<button id="bad-ui">UI</button>' : ''}
    <ul id="editViewList1">${viewList.data.map((v, i) => `<li data-vid="${v.id}" class="${i === 0 ? 'active' : ''}">${v.name}</li>`).join('')}</ul>
    <script>const canvas=document.querySelector('canvas'),ctx=canvas.getContext('2d');
    function paint(id){ctx.fillStyle='rgb('+(30+(${identical ? 0 : 'id-101'})*9)+',80,150)';ctx.fillRect(0,0,100,100);}
    paint(101);
    document.querySelectorAll('[data-vid]').forEach(li=>li.onclick=()=>{document.querySelectorAll('[data-vid]').forEach(x=>x.classList.remove('active'));li.classList.add('active');setTimeout(()=>paint(Number(li.dataset.vid)),35);});</script>`);
  return { page, browser, out, viewList };
}

test('exports all eleven views as full canvas images without overlaid HTML controls',
  { skip: !runtime.playwright, timeout: 20000 }, async t => {
    const f = await fixture(t, 11, { overlay: true });
    const result = await exportEffects(f.page, { viewList: f.viewList, out: f.out, renderWaitMs: 100 });
    assert.equal(result.status, 'complete');
    assert.equal(result.exported_count, 11);
    assert.equal(new Set(result.effects.map(x => x.view_id)).size, 11);
    for (const [i, effect] of result.effects.entries()) {
      const bytes = await fs.readFile(path.join(f.out, effect.file));
      assert.deepEqual(dimensions(bytes), { width: 100, height: 100, extension: 'png' });
      const pixel = await f.page.evaluate(async data => {
        const img = new Image(); img.src = 'data:image/png;base64,' + data; await img.decode();
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 100;
        const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0);
        return Array.from(ctx.getImageData(10, 10, 1, 1).data);
      }, bytes.toString('base64'));
      assert.deepEqual(pixel, [30 + i * 9, 80, 150, 255]); // The red HTML button must never enter an output image.
    }
    assert.equal(await f.page.locator('li.active').getAttribute('data-vid'), '101');
    assert.equal(f.browser.isConnected(), true);
    const html = await fs.readFile(path.join(f.out, 'effects.html'), 'utf8');
    assert.equal((html.match(/<figure>/g) || []).length, 11);
  });

test('different view IDs are retained even when their pixels are identical',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t, 2, { identical: true });
    const result = await exportEffects(f.page, { viewList: f.viewList, out: f.out, renderWaitMs: 100 });
    assert.equal(result.effects.length, 2);
    assert.equal(result.effects[0].sha256, result.effects[1].sha256);
    assert.notEqual(result.effects[0].file, result.effects[1].file);
  });

test('missing template views fail before a subset can be called complete',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t, 2);
    f.viewList.data.push({ id: 999, name: 'missing' });
    await assert.rejects(exportEffects(f.page, { viewList: f.viewList, out: f.out }),
      { code: 'EFFECT_VIEW_LIST_MISMATCH' });
    assert.equal((await fs.readdir(f.out)).length, 0);
  });

function cuaAdapter(page, screenshot) {
  return { playwright: page, url: () => page.url(), getAXState: async () => '',
    screenshot: screenshot || (options => page.screenshot({ ...options, type: 'jpeg' })) };
}

test('CUA fallback captures only the canvas rectangle and records actual JPEG dimensions',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t, 3);
    const result = await exportEffects(cuaAdapter(f.page), { viewList: f.viewList, out: f.out, renderWaitMs: 100 });
    assert.equal(result.effects.length, 3);
    for (const effect of result.effects) {
      assert.equal(effect.method, 'canvas_screenshot');
      assert.deepEqual(dimensions(await fs.readFile(path.join(f.out, effect.file))),
        { width: 100, height: 100, extension: 'jpg' });
    }
  });

test('CUA fallback refuses a canvas overlapped by UI rather than baking controls into images',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t, 2, { overlay: true });
    await assert.rejects(exportEffects(cuaAdapter(f.page), { viewList: f.viewList, out: f.out, renderWaitMs: 100 }),
      { code: 'EFFECT_EXPORT_INCOMPLETE' });
    const manifest = JSON.parse(await fs.readFile(path.join(f.out, 'effects-manifest.json')));
    assert.equal(manifest.status, 'incomplete');
    assert.equal(manifest.exported_count, 0);
    assert.ok(manifest.errors.every(error => error.code === 'EFFECT_CANVAS_OVERLAPPED_BY_UI'));
  });

test('CUA full-page capture includes a canvas extending below the viewport without cropping it',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t, 1);
    await f.page.locator('#editViewBox1').evaluate(element => { element.style.top = '550px'; });
    const result = await exportEffects(cuaAdapter(f.page), { viewList: f.viewList, out: f.out, renderWaitMs: 100 });
    assert.equal(result.effects.length, 1);
    assert.deepEqual(dimensions(await fs.readFile(path.join(f.out, result.effects[0].file))),
      { width: 100, height: 100, extension: 'jpg' });
  });

test('one failed view does not discard other views or report a complete delivery',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t, 3);
    const tab = cuaAdapter(f.page, async options => {
      if (await f.page.locator('li.active').getAttribute('data-vid') === '102') throw Error('fixture capture failure');
      return f.page.screenshot({ ...options, type: 'jpeg' });
    });
    await assert.rejects(exportEffects(tab, { viewList: f.viewList, out: f.out, renderWaitMs: 100 }),
      { code: 'EFFECT_EXPORT_INCOMPLETE' });
    const manifest = JSON.parse(await fs.readFile(path.join(f.out, 'effects-manifest.json')));
    assert.equal(manifest.exported_count, 2);
    assert.deepEqual(manifest.missing_view_ids, ['102']);
    assert.equal(await f.page.locator('li.active').getAttribute('data-vid'), '101');
    assert.equal(f.browser.isConnected(), true);
  });

test('all images captured during an artwork change cannot be finalized as one design',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t, 2);
    const tab = cuaAdapter(f.page, async options => {
      await f.page.locator('#editFlatList1 img').evaluate(img => { img.src += '%20'; });
      return f.page.screenshot({ ...options, type: 'jpeg' });
    });
    await assert.rejects(exportEffects(tab, { viewList: f.viewList, out: f.out, renderWaitMs: 100 }),
      { code: 'EFFECT_EXPORT_INCOMPLETE' });
    const manifest = JSON.parse(await fs.readFile(path.join(f.out, 'effects-manifest.json')));
    assert.equal(manifest.exported_count, 2);
    assert.equal(manifest.status, 'incomplete');
    assert.deepEqual(manifest.errors, [{ code: 'EFFECT_DESIGN_CHANGED_DURING_EXPORT' }]);
  });
