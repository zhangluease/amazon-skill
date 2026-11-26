// Use an existing Playwright Page or CUA Tab. No browser/session creation or API guesses.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { dimensions, viewIds, readFlats, readDesignFingerprint } from './podqhy_effects.mjs';

const MODAL = '#editSaveTemplate1';
const SUCCESS = '#saveSuccessTemplate1';
function failure(code) { return Object.assign(new Error(code), { code }); }
async function observe(tab) { if (tab.getAXState) await tab.getAXState({ emit: false }); }
function editorKey(value) {
  let url;
  try { url = new URL(value); } catch { throw failure('DESIGN_EDITOR_REQUIRED'); }
  if (!['podqhy.com', 'www.podqhy.com'].includes(url.hostname) || url.protocol !== 'https:'
      || url.pathname !== '/edit' || !/^[1-9]\d*$/.test(url.searchParams.get('colorId') || ''))
    throw failure('DESIGN_EDITOR_REQUIRED');
  url.searchParams.sort();
  return url.searchParams.toString();
}

function artworkSources(thumbnail) {
  let svg;
  if (thumbnail.startsWith('data:image/svg+xml;base64,'))
    svg = Buffer.from(thumbnail.split(',')[1], 'base64').toString('utf8');
  else if (thumbnail.startsWith('data:image/svg+xml,')) svg = decodeURIComponent(thumbnail.split(',').slice(1).join(','));
  else throw failure('COPY_THUMBNAIL_UNSUPPORTED');
  // Only examine editable artwork, not masks or product background shapes.
  const opening = /<g\b[^>]*\bclass=["'][^"']*\belementObj\b[^"']*["'][^>]*>/.exec(svg);
  if (!opening) throw failure('COPY_THUMBNAIL_UNSUPPORTED');
  if (/\/>$/.test(opening[0])) return [];
  const groups = /<\/?g\b[^>]*>/g;
  const start = opening.index + opening[0].length;
  groups.lastIndex = start;
  let depth = 1, content;
  for (let tag; (tag = groups.exec(svg));) {
    if (tag[0].startsWith('</')) depth--;
    else if (!/\/>$/.test(tag[0])) depth++;
    if (depth === 0) { content = svg.slice(start, tag.index); break; }
  }
  if (content === undefined) throw failure('COPY_THUMBNAIL_UNSUPPORTED');
  if (/<(?:text|path|rect|circle|ellipse|polygon|polyline|line|use)\b/.test(content))
    throw failure('COPY_NON_IMAGE_LAYERS_CHECK_PAGE');
  return Array.from(content.matchAll(/<image\b[^>]*\b(?:xlink:)?href\s*=\s*["']([^"']+)["']/g), match => match[1]).sort();
}

async function copyArtworkToAll(tab, { flatList, sourceSurfaceId } = {}) {
  editorKey(await tab.url());
  const api = tab.playwright || tab;
  const ids = (flatList?.data || []).filter(flat => flat.config?.edit).map(flat => String(flat.id));
  if (!ids.length || ids.some(id => !/^[1-9]\d*$/.test(id)) || new Set(ids).size !== ids.length)
    throw failure('COPY_TEMPLATE_INVALID');
  const before = await readFlats(api, { editableOnly: true });
  if (before.length !== ids.length || new Set(before.map(flat => flat.id)).size !== ids.length
      || before.some(flat => !ids.includes(flat.id))) throw failure('COPY_SURFACE_LIST_MISMATCH');
  const source = before.find(flat => flat.active);
  if (!source || source.id !== String(sourceSurfaceId)) throw failure('COPY_SOURCE_NOT_ACTIVE');
  const expected = artworkSources(source.thumbnail);
  if (!expected.length) throw failure('COPY_SOURCE_EMPTY');
  const signature = JSON.stringify(expected);
  const targets = before.filter(flat => flat.id !== source.id);
  const existing = targets.map(flat => artworkSources(flat.thumbnail));
  const alreadyCopied = existing.every(assets => JSON.stringify(assets) === signature);
  if (!alreadyCopied && existing.some(assets => assets.length)) throw failure('COPY_TARGET_NOT_EMPTY');
  if (!alreadyCopied) {
    await api.locator('#editFlatCopy1 button[data-toggle="dropdown"]').click();
    await observe(tab);
    await api.locator('#editFlatCopy1 input[name="copyType"][value="0"]').check();
    await observe(tab);
    await api.locator('#editFlatCopy1 [data-content-copy] a[data-value="0"]').click();
    await observe(tab);
    let verified = false;
    for (let attempt = 0; attempt < 20; attempt++) {
      const after = await readFlats(api, { editableOnly: true });
      verified = after.length === ids.length && new Set(after.map(flat => flat.id)).size === ids.length
        && after.every(flat => ids.includes(flat.id) && JSON.stringify(artworkSources(flat.thumbnail)) === signature);
      if (verified) break;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    if (!verified) throw failure('COPY_NOT_VERIFIED_DO_NOT_REPEAT');
  }
  return { status: 'copied_pending_surface_review', already_copied: alreadyCopied,
    source_surface_id: source.id, surface_ids: ids, position: 'center',
    requires_per_surface_placement_and_coverage_check: true };
}

async function validateEffects(manifestFile, currentUrl, api) {
  const manifest = JSON.parse(await fs.readFile(manifestFile, 'utf8'));
  const expected = (manifest.expected_view_ids || []).map(String);
  if (manifest.stage !== 'final' || manifest.status !== 'complete' || !expected.length
      || new Set(expected).size !== expected.length || manifest.expected_count !== expected.length
      || manifest.exported_count !== expected.length || manifest.effects?.length !== expected.length
      || manifest.missing_view_ids?.length !== 0 || manifest.errors?.length)
    throw failure('DESIGN_FINAL_EFFECTS_INCOMPLETE');
  if (editorKey(manifest.source_url) !== editorKey(currentUrl)) throw failure('DESIGN_PRODUCT_MISMATCH');
  if (!/^[a-f0-9]{64}$/.test(manifest.design_fingerprint || ''))
    throw failure('DESIGN_EFFECTS_NEED_FRESH_EXPORT');
  const actual = await api.locator('#editViewList1 li[data-vid]').evaluateAll(items => items.map(item => item.getAttribute('data-vid')));
  viewIds({ data: expected.map(id => ({ id })) });
  if (actual.length !== expected.length || new Set(actual).size !== expected.length
      || actual.some(id => !expected.includes(id))) throw failure('DESIGN_VIEW_LIST_MISMATCH');
  const minimum = manifest.minimum_side;
  const ratioPattern = /^([1-9]\d*):([1-9]\d*)$/;
  const commonRatio = ratioPattern.exec(manifest.output_aspect_ratio || '');
  const mixedRatios = minimum === 2000 && manifest.output_aspect_ratio === null && manifest.requested_ratio === null;
  if ((!commonRatio && !mixedRatios) || (minimum !== undefined && minimum !== 2000)
      || new Set(manifest.effects.map(effect => String(effect.view_id))).size !== expected.length)
    throw failure('DESIGN_EFFECTS_INVALID');
  const base = path.dirname(path.resolve(manifestFile));
  for (const effect of manifest.effects) {
    const ratio = commonRatio || ratioPattern.exec(effect.aspect_ratio || '');
    const file = path.resolve(base, effect.file);
    if (!file.startsWith(base + path.sep) || !expected.includes(String(effect.view_id))) throw failure('DESIGN_EFFECTS_INVALID');
    const bytes = await fs.readFile(file), size = dimensions(bytes);
    if (!ratio || size.width * Number(ratio[2]) !== size.height * Number(ratio[1])
        || (minimum && Math.min(size.width, size.height) < minimum)
        || size.width !== effect.width || size.height !== effect.height
        || crypto.createHash('sha256').update(bytes).digest('hex') !== effect.sha256)
      throw failure('DESIGN_EFFECTS_INVALID');
  }
  return manifest;
}

async function verifyCurrentDesign(tab, sourceUrl, expectedFingerprint) {
  if (await tab.url() !== sourceUrl || await readDesignFingerprint(tab) !== expectedFingerprint)
    throw failure('DESIGN_CHANGED_REEXPORT_EFFECTS');
}

async function saveDesign(tab, { name, effectsManifest, out, allowIncompleteCoverage = false,
  transparentBackground, timeoutMs = 30000 } = {}) {
  name = String(name || '').trim();
  if (!name || /^(new|untitled|未命名)$/i.test(name)) throw failure('DESIGN_NAME_REQUIRED');
  if (!effectsManifest || !out) throw failure('DESIGN_OUTPUTS_REQUIRED');
  if (!Number.isFinite(timeoutMs) || timeoutMs < 100 || timeoutMs > 60000) throw failure('DESIGN_TIMEOUT_INVALID');
  const api = tab.playwright || tab, sourceUrl = await tab.url();
  const effects = await validateEffects(effectsManifest, sourceUrl, api);
  const effectsHash = crypto.createHash('sha256').update(JSON.stringify(effects)).digest('hex');
  out = path.resolve(out);
  await fs.mkdir(out, { recursive: true });
  const receiptFile = path.join(out, 'design-save.json'), lockFile = path.join(out, '.design-save.lock');
  let lock;
  try { lock = await fs.open(lockFile, 'wx'); } catch { throw failure('DESIGN_SAVE_LOCKED'); }
  let receipt, submitted = false;
  const write = () => fs.writeFile(receiptFile, JSON.stringify(receipt, null, 2) + '\n');
  try {
    let prior;
    try { prior = JSON.parse(await fs.readFile(receiptFile, 'utf8')); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (prior) {
      if (prior.status === 'saved' && prior.name === name && prior.source_url === sourceUrl
          && prior.effects_manifest === path.resolve(effectsManifest) && prior.effects_sha256 === effectsHash) return prior;
      if (prior.status !== 'not_submitted') throw failure('DESIGN_SAVE_ALREADY_ATTEMPTED_CHECK_ACCOUNT');
    }
    await verifyCurrentDesign(tab, sourceUrl, effects.design_fingerprint);
    if (await api.locator(SUCCESS).isVisible()) throw failure('DESIGN_OLD_SUCCESS_DIALOG_VISIBLE');
    if (!await api.locator(MODAL).isVisible()) {
      await api.locator('#editSaveDesign1').click();
      await observe(tab);
    }
    await api.locator(MODAL).waitFor({ state: 'visible', timeout: timeoutMs });
    const field = api.locator(`${MODAL} input[name="name"]`);
    await field.click(); // The site's focus handler removes readonly.
    await field.fill(name);
    await observe(tab);
    if (transparentBackground !== undefined) {
      await api.locator(`${MODAL} input[name="type"]`).setChecked(Boolean(transparentBackground));
      await observe(tab);
    }
    if (!allowIncompleteCoverage && await api.locator(`${MODAL} .alert`).filter({ hasText: '定制区域未铺满' }).isVisible())
      throw failure('DESIGN_NOT_FULL_CHECK_SURFACES');
    await verifyCurrentDesign(tab, sourceUrl, effects.design_fingerprint);
    receipt = { status: 'submitted', name, source_url: sourceUrl,
      effects_manifest: path.resolve(effectsManifest), effect_count: effects.exported_count,
      effects_sha256: effectsHash,
      output_aspect_ratio: effects.output_aspect_ratio, submitted_at: new Date().toISOString(),
      verification: null };
    await write();
    submitted = true;
    await api.locator(`${MODAL} button[data-action="save"]`).click();
    await observe(tab);
    await api.locator(SUCCESS).waitFor({ state: 'visible', timeout: timeoutMs });
    receipt.status = 'saved';
    receipt.verification = 'site_save_success_dialog';
    receipt.saved_at = new Date().toISOString();
    await write();
    return receipt;
  } catch (error) {
    if (submitted) {
      receipt.status = 'unknown';
      receipt.error = 'DESIGN_SAVE_OUTCOME_UNCONFIRMED_CHECK_ACCOUNT';
      await write();
      throw failure(receipt.error);
    }
    throw error;
  } finally {
    await lock.close();
    await fs.unlink(lockFile);
  }
}

export { copyArtworkToAll, saveDesign };
