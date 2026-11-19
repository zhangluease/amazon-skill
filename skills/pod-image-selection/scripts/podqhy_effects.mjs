// Export every rendered PODQHY view from an existing Playwright Page or CUA Tab.
// This module never launches/closes a browser or saves/submits a design.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';

const LIST = '#editViewList1 li[data-vid]';
const CANVAS = '#editViewBox1 canvas';
function failure(code) { return Object.assign(new Error(code), { code }); }
function viewIds(views) {
  if (!Array.isArray(views?.data) || !views.data.length) throw failure('EFFECT_VIEWS_REQUIRED');
  const ids = views.data.map(view => String(view.id));
  if (ids.some(id => !/^[1-9]\d*$/.test(id)) || new Set(ids).size !== ids.length)
    throw failure('EFFECT_VIEW_IDS_INVALID');
  return ids;
}

function dimensions(bytes) {
  if (bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) && bytes.length >= 24)
    return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), extension: 'png' };
  if (bytes[0] === 255 && bytes[1] === 216) {
    let offset = 2;
    while (offset + 4 <= bytes.length && bytes[offset] === 255) {
      while (bytes[offset] === 255) offset++;
      const marker = bytes[offset++];
      if (marker === 217 || marker === 218) break;
      if (marker === 1 || marker >= 208 && marker <= 215) continue;
      const length = bytes.readUInt16BE(offset);
      if (length < 2 || offset + length > bytes.length) break;
      if ([192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207].includes(marker) && length >= 8)
        return { width: bytes.readUInt16BE(offset + 5), height: bytes.readUInt16BE(offset + 3), extension: 'jpg' };
      offset += length;
    }
  }
  throw failure('EFFECT_IMAGE_INVALID');
}

// Geometry comes from the actual canvas. Never use a fixed right-edge crop.
async function captureCanvas(tab, api) {
  if (!tab.playwright) {
    const result = await api.locator(CANVAS).evaluate(canvas => ({
      data: canvas.toDataURL('image/png'), width: canvas.width, height: canvas.height
    }));
    if (!result.data.startsWith('data:image/png;base64,')) throw failure('EFFECT_CANVAS_EMPTY');
    return { bytes: Buffer.from(result.data.split(',')[1], 'base64'),
      canvas_width: result.width, canvas_height: result.height, method: 'canvas_png' };
  }
  // CUA's read-only DOM bridge does not expose canvas.toDataURL. Capture only its rectangle.
  const geometry = await api.evaluate(() => {
    const canvas = document.querySelector('#editViewBox1 canvas');
    if (!canvas) return null;
    const r = canvas.getBoundingClientRect();
    const overlaps = Array.from(document.querySelectorAll('button,a,[role="button"],#editViewList1'))
      .filter(element => {
        const b = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return b.width > 0 && b.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
          && style.opacity !== '0' && Math.min(b.right, r.right) - Math.max(b.left, r.left) > 1
          && Math.min(b.bottom, r.bottom) - Math.max(b.top, r.top) > 1;
      }).length;
    return { x: r.x, y: r.y, width: r.width, height: r.height,
      canvas_width: canvas.width, canvas_height: canvas.height, overlaps };
  });
  if (!geometry || geometry.width < 1 || geometry.height < 1) throw failure('EFFECT_CANVAS_NOT_VISIBLE');
  if (geometry.overlaps) throw failure('EFFECT_CANVAS_OVERLAPPED_BY_UI');
  if (geometry.x < 0 || geometry.y < 0) throw failure('EFFECT_CANVAS_OUTSIDE_VIEWPORT');
  // Full-page capture includes the canvas's bottom even when the viewport is slightly shorter.
  const bytes = await tab.screenshot({ fullPage: true, clip: { x: geometry.x, y: geometry.y,
    width: geometry.width, height: geometry.height } });
  return { bytes: Buffer.from(bytes), canvas_width: geometry.canvas_width,
    canvas_height: geometry.canvas_height, capture_width: geometry.width,
    capture_height: geometry.height, method: 'canvas_screenshot' };
}

function escape(value) {
  return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
    '"': '&quot;', "'": '&#39;' }[char]));
}
function gallery(manifest) {
  const cards = manifest.effects.map(effect => `<figure><a href="${effect.file}" target="_blank"><img src="${effect.file}" alt="${escape(effect.name)}"></a><figcaption>${effect.index}. ${escape(effect.name)} · ${effect.width} × ${effect.height} <a href="${effect.file}" download>下载</a></figcaption></figure>`).join('\n');
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>产品全部效果图</title><style>body{font:16px system-ui;margin:24px;background:#f4f5f7;color:#222}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px}figure{margin:0;padding:12px;background:white;border-radius:8px}img{display:block;width:100%;height:auto}figcaption{padding-top:10px}a{color:#245dab}</style><h1>产品效果图 ${manifest.exported_count}/${manifest.expected_count}</h1><p>${manifest.status === 'complete' ? '全部视角已导出，等待确认效果。' : '导出未完成，仍有缺失视角。'}</p><main>${cards}</main></html>`;
}

async function exportEffects(tab, { viewList, out, renderWaitMs = 300 } = {}) {
  const expected = viewIds(viewList), api = tab.playwright || tab;
  out = path.resolve(out);
  if (!Number.isFinite(renderWaitMs) || renderWaitMs < 100 || renderWaitMs > 5000)
    throw failure('EFFECT_RENDER_WAIT_INVALID');
  const listed = await api.locator(LIST).evaluateAll(items => items.map(item => ({
    id: item.getAttribute('data-vid'), active: item.classList.contains('active')
  })));
  const actual = listed.map(item => item.id);
  if (actual.length !== expected.length || new Set(actual).size !== actual.length
      || actual.some(id => !expected.includes(id))) throw failure('EFFECT_VIEW_LIST_MISMATCH');
  await fs.mkdir(out, { recursive: true });
  const manifest = { status: 'incomplete', source_url: await tab.url(),
    expected_count: expected.length, expected_view_ids: actual,
    exported_count: 0, missing_view_ids: actual.slice(), effects: [], errors: [] };
  const original = listed.find(item => item.active)?.id;
  try {
    for (const [index, id] of actual.entries()) {
      try {
        await api.locator(`${LIST}[data-vid="${id}"]`).click();
        if (tab.getAXState) await tab.getAXState({ emit: false });
        // The site draws its already-loaded view layers on a 10 fps canvas loop.
        await new Promise(resolve => setTimeout(resolve, renderWaitMs));
        if (!await api.locator(`${LIST}[data-vid="${id}"]`).evaluate(item => item.classList.contains('active')))
          throw failure('EFFECT_VIEW_NOT_ACTIVE');
        const captured = await captureCanvas(tab, api);
        const size = dimensions(captured.bytes);
        if (!size.width || !size.height || Math.abs(size.width / size.height
            - captured.canvas_width / captured.canvas_height) > 0.01)
          throw failure('EFFECT_IMAGE_ASPECT_MISMATCH');
        if (captured.method === 'canvas_png' && (size.width !== captured.canvas_width || size.height !== captured.canvas_height))
          throw failure('EFFECT_IMAGE_SIZE_MISMATCH');
        if (captured.method === 'canvas_screenshot' && (Math.abs(size.width - captured.capture_width) > 1
            || Math.abs(size.height - captured.capture_height) > 1)) throw failure('EFFECT_IMAGE_CAPTURE_CLIPPED');
        const file = `effect-${String(index + 1).padStart(2, '0')}-${id}.${size.extension}`;
        await fs.writeFile(path.join(out, file), captured.bytes);
        manifest.effects.push({ index: index + 1, view_id: id,
          name: String(viewList.data.find(view => String(view.id) === id).name || `视角 ${index + 1}`),
          file, width: size.width, height: size.height, canvas_width: captured.canvas_width,
          canvas_height: captured.canvas_height, method: captured.method,
          sha256: crypto.createHash('sha256').update(captured.bytes).digest('hex') });
      } catch (error) {
        manifest.errors.push({ view_id: id, code: error.code || 'EFFECT_CAPTURE_FAILED' });
      }
    }
  } finally {
    if (original) {
      try {
        await api.locator(`${LIST}[data-vid="${original}"]`).click();
        if (tab.getAXState) await tab.getAXState({ emit: false });
      } catch { manifest.restore_view_failed = true; }
    }
    manifest.exported_count = manifest.effects.length;
    manifest.missing_view_ids = actual.filter(id => !manifest.effects.some(effect => effect.view_id === id));
    if (!manifest.missing_view_ids.length) manifest.status = 'complete';
    await fs.writeFile(path.join(out, 'effects-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
    await fs.writeFile(path.join(out, 'effects.html'), gallery(manifest));
  }
  if (manifest.status !== 'complete') throw Object.assign(failure('EFFECT_EXPORT_INCOMPLETE'), { manifest });
  return manifest;
}

export { exportEffects, viewIds, dimensions };
