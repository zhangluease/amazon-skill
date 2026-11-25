const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { findPlaywright } = require('../skills/pod-image-selection/scripts/environment.cjs');
const { closeOwnedBrowser } = require('../skills/pod-image-selection/scripts/browser_lifecycle.cjs');
const runtime = findPlaywright();
let copyArtworkToAll, saveDesign, readDesignFingerprint;
test.before(async () => {
  ({ copyArtworkToAll, saveDesign } = await import('../skills/pod-image-selection/scripts/podqhy_design.mjs'));
  ({ readDesignFingerprint } = await import('../skills/pod-image-selection/scripts/podqhy_effects.mjs'));
});
const url = 'https://www.podqhy.com/edit?colorId=999';
const pixel = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=';
const svg = content => 'data:image/svg+xml;base64,' + Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><g class="elementObj">${content}</g></svg>`).toString('base64');
const filled = svg(`<image href="data:image/png;base64,${pixel}" width="40" height="40"/>`);
const empty = svg('');

async function fixture(t, { sourceEmpty = false, occupied = false, coverage = false, success = true } = {}) {
  const out = await fs.mkdtemp(path.join(os.tmpdir(), 'pod-design-'));
  const browser = await runtime.playwright.chromium.launch({ headless: true });
  t.after(async () => { await closeOwnedBrowser(browser); await fs.rm(out, { recursive: true, force: true }); });
  const page = await browser.newPage();
  await page.route(url, route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<html><meta charset="utf-8"><body>
    <ul id="editFlatList1">${[1, 2, 3].map(id => `<li data-fid="${id}" data-edit="1" class="${id === 1 ? 'active' : ''}"><img src="${id === 1 ? sourceEmpty ? empty : filled : occupied ? filled : empty}"></li>`).join('')}</ul>
    <div id="editFlatCopy1"><button data-toggle="dropdown" onclick="document.querySelector('#copy-menu').hidden=false">复制至</button>
      <div id="copy-menu" hidden><input type="radio" name="copyType" value="0" checked>位置居中
        <div data-content-copy><a data-value="0" href="#">所有</a></div></div></div>
    <ul id="editViewList1"><li data-vid="101">效果 1</li></ul>
    <button id="editSaveDesign1">保存</button>
    <div id="editSaveTemplate1" style="display:none"><input name="name" readonly onfocus="this.removeAttribute('readonly')">
      <input name="type" type="checkbox"><div class="alert" style="display:${coverage ? 'block' : 'none'}">定制区域未铺满，请检查</div>
      <button data-action="save">保存设计</button></div>
    <div id="saveSuccessTemplate1" style="display:none">保存成功！</div>
    <script>window.copyClicks=0;window.saveClicks=0;
    document.querySelector('[data-content-copy] a').onclick=event=>{event.preventDefault();window.copyClicks++;
      const source=document.querySelector('li.active img').getAttribute('src');
      document.querySelectorAll('#editFlatList1 img').forEach(img=>img.setAttribute('src',source));};
    document.querySelector('#editSaveDesign1').onclick=()=>document.querySelector('#editSaveTemplate1').style.display='block';
    document.querySelector('[data-action=save]').onclick=()=>{window.saveClicks++;
      ${success ? "setTimeout(()=>{document.querySelector('#editSaveTemplate1').style.display='none';document.querySelector('#saveSuccessTemplate1').style.display='block';},20);" : ''}};
    </script></body></html>` }));
  await page.goto(url);
  const file = 'effect-01-101.png', bytes = Buffer.from(pixel, 'base64');
  await fs.mkdir(path.join(out, 'final-effects'));
  await fs.writeFile(path.join(out, 'final-effects', file), bytes);
  const manifest = { stage: 'final', status: 'complete', source_url: url, expected_view_ids: ['101'],
    design_fingerprint: await readDesignFingerprint(page),
    expected_count: 1, exported_count: 1, missing_view_ids: [], output_aspect_ratio: '1:1',
    effects: [{ view_id: '101', file, width: 1, height: 1, sha256: crypto.createHash('sha256').update(bytes).digest('hex') }] };
  const effectsManifest = path.join(out, 'final-effects/effects-manifest.json');
  await fs.writeFile(effectsManifest, JSON.stringify(manifest));
  const flatList = { data: [1, 2, 3].map(id => ({ id, config: { edit: 1, flatWidth: id * 40, flatHeight: 40 } })) };
  return { page, browser, out, effectsManifest, manifest, flatList };
}

test('one copy distributes one placed asset to all differently sized faces and is idempotent',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t);
    const copied = await copyArtworkToAll(f.page, { flatList: f.flatList, sourceSurfaceId: 1 });
    assert.equal(copied.status, 'copied_pending_surface_review');
    assert.deepEqual(copied.surface_ids, ['1', '2', '3']);
    assert.equal(await f.page.evaluate(() => window.copyClicks), 1);
    await copyArtworkToAll(f.page, { flatList: f.flatList, sourceSurfaceId: 1 });
    assert.equal(await f.page.evaluate(() => window.copyClicks), 1);
    assert.equal(f.browser.isConnected(), true);
  });

test('empty source and mixed occupied targets stop before copying',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t, { sourceEmpty: true });
    await assert.rejects(copyArtworkToAll(f.page, { flatList: f.flatList, sourceSurfaceId: 1 }), { code: 'COPY_SOURCE_EMPTY' });
    await f.page.locator('li.active img').evaluate((img, src) => img.setAttribute('src', src), filled);
    await f.page.locator('li[data-fid="2"] img').evaluate((img, src) => img.setAttribute('src', src), filled);
    await assert.rejects(copyArtworkToAll(f.page, { flatList: f.flatList, sourceSurfaceId: 1 }), { code: 'COPY_TARGET_NOT_EMPTY' });
    assert.equal(await f.page.evaluate(() => window.copyClicks), 0);
  });

test('saves with an image description, waits for actual success, writes a receipt, and never resubmits',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t);
    const options = { name: '蓝白格纹-宠物窝', effectsManifest: f.effectsManifest, out: f.out };
    const receipt = await saveDesign(f.page, options);
    assert.equal(receipt.status, 'saved');
    assert.equal(receipt.name, options.name);
    assert.equal(receipt.verification, 'site_save_success_dialog');
    assert.equal(await f.page.evaluate(() => window.saveClicks), 1);
    assert.equal((await saveDesign(f.page, options)).status, 'saved');
    assert.equal(await f.page.evaluate(() => window.saveClicks), 1);
    assert.equal(JSON.parse(await fs.readFile(path.join(f.out, 'design-save.json'))).status, 'saved');
    assert.equal(f.browser.isConnected(), true);
  });

test('text-only target content is never treated as an empty face',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t);
    await f.page.locator('li[data-fid="2"] img').evaluate((img, src) => img.setAttribute('src', src), svg('<text>Existing artwork</text>'));
    await assert.rejects(copyArtworkToAll(f.page, { flatList: f.flatList, sourceSurfaceId: 1 }),
      { code: 'COPY_NON_IMAGE_LAYERS_CHECK_PAGE' });
    assert.equal(await f.page.evaluate(() => window.copyClicks), 0);
  });

test('reuses an already-open naming modal and focuses the readonly field',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t);
    await f.page.locator('#editSaveDesign1').click();
    const tab = { playwright: f.page, url: () => f.page.url(), getAXState: async () => '' };
    const receipt = await saveDesign(tab, { name: '粉色花朵-宠物窝', effectsManifest: f.effectsManifest,
      out: f.out, transparentBackground: true });
    assert.equal(receipt.status, 'saved');
    assert.equal(await f.page.locator('input[name=type]').isChecked(), true);
  });

test('missing views, wrong product, or damaged final files stop before submission',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t);
    const options = { name: '蓝白格纹', effectsManifest: f.effectsManifest, out: f.out };
    for (const manifest of [{ ...f.manifest, exported_count: 0 },
      { ...f.manifest, source_url: 'https://www.podqhy.com/edit?colorId=998' },
      { ...f.manifest, expected_view_ids: ['102'], effects: [{ ...f.manifest.effects[0], view_id: '102' }] }]) {
      await fs.writeFile(f.effectsManifest, JSON.stringify(manifest));
      await assert.rejects(saveDesign(f.page, options));
    }
    await fs.writeFile(f.effectsManifest, JSON.stringify(f.manifest));
    await fs.writeFile(path.join(f.out, 'final-effects', f.manifest.effects[0].file), 'damaged');
    await assert.rejects(saveDesign(f.page, options));
    assert.equal(await f.page.evaluate(() => window.saveClicks), 0);
  });

test('same product and views cannot save when artwork moved after effects were exported',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t);
    const moved = svg(`<image href="data:image/png;base64,${pixel}" x="5" width="40" height="40"/>`);
    await f.page.locator('li.active img').evaluate((img, src) => img.setAttribute('src', src), moved);
    await assert.rejects(saveDesign(f.page, { name: '蓝白格纹', effectsManifest: f.effectsManifest, out: f.out }),
      { code: 'DESIGN_CHANGED_REEXPORT_EFFECTS' });
    assert.equal(await f.page.evaluate(() => window.saveClicks), 0);
  });

test('full-print coverage warning blocks submission until checked',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t, { coverage: true });
    const options = { name: '留白花朵', effectsManifest: f.effectsManifest, out: f.out };
    await assert.rejects(saveDesign(f.page, options), { code: 'DESIGN_NOT_FULL_CHECK_SURFACES' });
    assert.equal(await f.page.evaluate(() => window.saveClicks), 0);
    assert.equal((await saveDesign(f.page, { ...options, allowIncompleteCoverage: true })).status, 'saved');
  });

test('uncertain save outcome is recorded and blocks automatic duplicate submission',
  { skip: !runtime.playwright, timeout: 10000 }, async t => {
    const f = await fixture(t, { success: false });
    const options = { name: '蓝白格纹', effectsManifest: f.effectsManifest, out: f.out, timeoutMs: 150 };
    await assert.rejects(saveDesign(f.page, options), { code: 'DESIGN_SAVE_OUTCOME_UNCONFIRMED_CHECK_ACCOUNT' });
    assert.equal(JSON.parse(await fs.readFile(path.join(f.out, 'design-save.json'))).status, 'unknown');
    await assert.rejects(saveDesign(f.page, options), { code: 'DESIGN_SAVE_ALREADY_ATTEMPTED_CHECK_ACCOUNT' });
    assert.equal(await f.page.evaluate(() => window.saveClicks), 1);
  });
