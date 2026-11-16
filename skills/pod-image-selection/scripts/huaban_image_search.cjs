const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { stateRoot } = require('./sellersprite_session.cjs');

const MAX_IMAGE_BYTES = 12 * 1024 * 1024;
const AMAZON_IMAGE_HOSTS = new Set(['m.media-amazon.com', 'images-na.ssl-images-amazon.com',
  'images-eu.ssl-images-amazon.com', 'images-fe.ssl-images-amazon.com']);

function imageError(code) { return Object.assign(Error(code), { code }); }

function defaultStateFile() { return path.join(stateRoot(), 'huaban-state.json'); }

function validateImageUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw imageError('IMAGE_REFERENCE_URL_UNSUPPORTED'); }
  if (url.protocol !== 'https:' || !AMAZON_IMAGE_HOSTS.has(url.hostname) || url.username || url.password
    || url.port && url.port !== '443' || url.hash || /[\r\n]/.test(value))
    throw imageError('IMAGE_REFERENCE_URL_UNSUPPORTED');
  return value;
}

function imageFormat(buffer) {
  if (buffer.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]))) return { ext: '.jpg', mimeType: 'image/jpeg' };
  if (buffer.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex'))) return { ext: '.png', mimeType: 'image/png' };
  if (['GIF87a', 'GIF89a'].includes(buffer.subarray(0, 6).toString('ascii'))) return { ext: '.gif', mimeType: 'image/gif' };
  if (buffer.subarray(0, 4).toString('ascii') === 'RIFF' && buffer.subarray(8, 12).toString('ascii') === 'WEBP')
    return { ext: '.webp', mimeType: 'image/webp' };
  throw imageError('IMAGE_REFERENCE_FORMAT_UNSUPPORTED');
}

async function readReference(options, dependencies = {}) {
  let buffer;
  if (options['image-url']) {
    const url = validateImageUrl(options['image-url']);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await (dependencies.fetch || fetch)(url, { redirect: 'error', signal: controller.signal });
      if (!response.ok || !response.body) throw imageError('IMAGE_REFERENCE_DOWNLOAD_FAILED');
      if (Number(response.headers.get('content-length')) > MAX_IMAGE_BYTES) throw imageError('IMAGE_REFERENCE_TOO_LARGE');
      let size = 0;
      const chunks = [];
      for await (const chunk of response.body) {
        size += chunk.length;
        if (size > MAX_IMAGE_BYTES) { controller.abort(); throw imageError('IMAGE_REFERENCE_TOO_LARGE'); }
        chunks.push(Buffer.from(chunk));
      }
      buffer = Buffer.concat(chunks);
    } catch (error) {
      if (/^IMAGE_REFERENCE_/.test(error.code || '')) throw error;
      throw imageError('IMAGE_REFERENCE_DOWNLOAD_FAILED');
    } finally { clearTimeout(timer); controller.abort(); }
  } else {
    try {
      const input = path.resolve(options.image);
      const stat = await fs.stat(input);
      if (!stat.isFile()) throw imageError('IMAGE_REFERENCE_NOT_READABLE');
      if (stat.size > MAX_IMAGE_BYTES) throw imageError('IMAGE_REFERENCE_TOO_LARGE');
      buffer = await fs.readFile(input);
    } catch (error) {
      if (/^IMAGE_REFERENCE_/.test(error.code || '')) throw error;
      throw imageError('IMAGE_REFERENCE_NOT_READABLE');
    }
  }
  if (buffer.length > MAX_IMAGE_BYTES) throw imageError('IMAGE_REFERENCE_TOO_LARGE');
  const format = imageFormat(buffer);
  const destination = path.resolve(options.out, 'reference-image' + format.ext);
  await fs.mkdir(path.dirname(destination), { recursive: true });
  await fs.writeFile(destination, buffer);
  return { buffer, ...format, provenance: { via: 'image_search', reference_image: destination,
    reference_sha256: crypto.createHash('sha256').update(buffer).digest('hex'),
    ...(options['image-url'] ? { source_image_url: options['image-url'] } : {}),
    ...(options['source-asin'] ? { source_asin: options['source-asin'] } : {}) } };
}

function imageSearchUrl(value) {
  let url;
  try { url = new URL(value); } catch { return false; }
  return url.origin === 'https://huaban.com' && url.pathname === '/similar'
    && Boolean(url.searchParams.get('bucket')) && Boolean(url.searchParams.get('key'))
    && ['width', 'height'].every(key => {
      const value = Number(url.searchParams.get(key));
      return Number.isFinite(value) && value > 0;
    });
}

async function submitReference(page, reference) {
  // The current uploader requires a logged-in Huaban user; anonymous clicks open SSO.
  if (await page.getByRole('button', { name: '登录/注册', exact: true }).isVisible())
    throw imageError('IMAGE_SEARCH_LOGIN_REQUIRED');
  await page.getByRole('button', { name: '以图搜索', exact: true }).click();
  const input = page.locator('input[type="file"]');
  await input.waitFor({ state: 'attached', timeout: 10000 });
  await input.setInputFiles({ name: 'reference-image' + reference.ext,
    mimeType: reference.mimeType, buffer: reference.buffer });
  try {
    await page.waitForURL(url => imageSearchUrl(url.href), { waitUntil: 'domcontentloaded', timeout: 30000 });
  } catch {
    if (await page.getByRole('button', { name: '登录/注册', exact: true }).isVisible().catch(() => false))
      throw imageError('IMAGE_SEARCH_LOGIN_REQUIRED');
    throw imageError('IMAGE_SEARCH_NOT_CONFIRMED');
  }
  if (!imageSearchUrl(page.url())) throw imageError('IMAGE_SEARCH_NOT_CONFIRMED');
  return page.url();
}

module.exports = { MAX_IMAGE_BYTES, imageError, validateImageUrl, imageFormat, readReference,
  imageSearchUrl, submitReference, defaultStateFile };
