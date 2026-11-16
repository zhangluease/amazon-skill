const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { argumentsFor } = require('../skills/pod-image-selection/scripts/huaban_browser.cjs');
const { readReference, imageFormat, validateImageUrl, imageSearchUrl, MAX_IMAGE_BYTES } = require('../skills/pod-image-selection/scripts/huaban_image_search.cjs');
const { saveState } = require('../skills/pod-image-selection/scripts/huaban_login.cjs');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=', 'base64');

test('image inputs carry an optional ASIN and cannot silently become keyword searches', () => {
  const options = argumentsFor(['--image', '/tmp/reference.png', '--source-asin', 'B000000001', '--out', '/tmp/out']);
  assert.equal(options.image, '/tmp/reference.png');
  assert.equal(options.query, undefined);
  assert.equal(options['source-asin'], 'B000000001');
  assert.throws(() => argumentsFor(['--image', '/tmp/reference.png', '--query', 'dog', '--out', '/tmp/out']));
  assert.throws(() => argumentsFor(['--image', '/tmp/reference.png', '--image-url', 'https://m.media-amazon.com/image.jpg', '--out', '/tmp/out']));
  assert.throws(() => argumentsFor(['--query', 'dog', '--source-asin', 'B000000001', '--out', '/tmp/out']));
});

test('reference URL downloads are limited to HTTPS Amazon image hosts', () => {
  const url = 'https://m.media-amazon.com/images/I/example._AC_US200_.jpg?public=1';
  assert.equal(validateImageUrl(url), url);
  for (const invalid of ['http://m.media-amazon.com/i.jpg', 'https://example.org/i.jpg',
    'https://m.media-amazon.com.example.org/i.jpg', 'https://user:password@m.media-amazon.com/i.jpg',
    'https://m.media-amazon.com:8443/i.jpg', 'file:///tmp/i.jpg'])
    assert.throws(() => validateImageUrl(invalid), { code: 'IMAGE_REFERENCE_URL_UNSUPPORTED' });
});

test('supported upload formats come from image bytes rather than a misleading filename', () => {
  assert.equal(imageFormat(png).mimeType, 'image/png');
  assert.equal(imageFormat(Buffer.from([0xff, 0xd8, 0xff, 0])).mimeType, 'image/jpeg');
  assert.equal(imageFormat(Buffer.from('GIF89a')).mimeType, 'image/gif');
  assert.equal(imageFormat(Buffer.from('RIFF0000WEBP')).mimeType, 'image/webp');
  assert.throws(() => imageFormat(Buffer.from('<svg></svg>')), { code: 'IMAGE_REFERENCE_FORMAT_UNSUPPORTED' });
});

test('only a Huaban image result URL with image identity and dimensions confirms upload', () => {
  assert.equal(imageSearchUrl('https://huaban.com/similar?bucket=hb&key=image&width=200&height=300'), true);
  for (const invalid of ['https://huaban.com/discovery', 'https://huaban.com/search?q=dog',
    'https://huaban.com/pins/11/similar', 'https://huaban.com/similar?bucket=hb&key=image',
    'https://huaban.com/similar?bucket=hb&key=image&width=Infinity&height=200',
    'https://example.org/similar?bucket=hb&key=image&width=200&height=200'])
    assert.equal(imageSearchUrl(invalid), false);
});

test('local references are preserved exactly with a digest and source ASIN', async t => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-image-test-'));
  t.after(() => fs.rm(tmp, { recursive: true, force: true }));
  const input = path.join(tmp, 'reference.txt');
  await fs.writeFile(input, png);
  const result = await readReference({ image: input, out: path.join(tmp, 'out'), 'source-asin': 'B000000001' });
  assert.equal(result.mimeType, 'image/png');
  assert.equal(result.provenance.via, 'image_search');
  assert.equal(result.provenance.source_asin, 'B000000001');
  assert.match(result.provenance.reference_sha256, /^[a-f0-9]{64}$/);
  assert.deepEqual(await fs.readFile(result.provenance.reference_image), png);
});

test('remote references keep the exact Amazon URL and bound streamed downloads', async t => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-image-download-test-'));
  t.after(() => fs.rm(tmp, { recursive: true, force: true }));
  const url = 'https://m.media-amazon.com/images/I/example._AC_US200_.jpg';
  const result = await readReference({ 'image-url': url, out: tmp }, { fetch: async (requested, options) => {
    assert.equal(requested, url);
    assert.equal(options.redirect, 'error');
    return { ok: true, headers: new Headers(), body: (async function* () { yield png; })() };
  } });
  assert.equal(result.provenance.source_image_url, url);
  assert.deepEqual(result.buffer, png);
  await assert.rejects(() => readReference({ 'image-url': url, out: tmp }, { fetch: async () => ({
    ok: true, headers: new Headers(), body: (async function* () { yield Buffer.alloc(MAX_IMAGE_BYTES); yield png; })()
  }) }), { code: 'IMAGE_REFERENCE_TOO_LARGE' });
});

test('failed or non-image downloads stop before upload without exposing response details', async t => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-image-failure-test-'));
  t.after(() => fs.rm(tmp, { recursive: true, force: true }));
  const options = { 'image-url': 'https://m.media-amazon.com/image.jpg', out: tmp };
  await assert.rejects(() => readReference(options, { fetch: async () => { throw Error('PRIVATE_DETAIL'); } }),
    { code: 'IMAGE_REFERENCE_DOWNLOAD_FAILED', message: 'IMAGE_REFERENCE_DOWNLOAD_FAILED' });
  await assert.rejects(() => readReference(options, { fetch: async () => ({ ok: true, headers: new Headers(),
    body: (async function* () { yield Buffer.from('<title>Access denied</title>'); })() }) }),
  { code: 'IMAGE_REFERENCE_FORMAT_UNSUPPORTED' });
});

test('saved login sessions contain only Huaban state and use private file permissions', async t => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-private-state-test-'));
  t.after(() => fs.rm(tmp, { recursive: true, force: true }));
  const file = path.join(tmp, 'state', 'huaban-state.json');
  await saveState({ cookies: [{ domain: '.huaban.com', name: 'fixture', value: 'allowed' },
    { domain: 'sso.example.org', name: 'unrelated', value: 'PRIVATE_UNRELATED' }],
  origins: [{ origin: 'https://huaban.com', localStorage: [] },
    { origin: 'https://sso.example.org', localStorage: [{ name: 'secret', value: 'PRIVATE_UNRELATED' }] }] }, file);
  const saved = await fs.readFile(file, 'utf8');
  assert.equal(saved.includes('PRIVATE_UNRELATED'), false);
  assert.equal(JSON.parse(saved).cookies.length, 1);
  assert.equal(JSON.parse(saved).origins.length, 1);
  if (process.platform !== 'win32') assert.equal((await fs.stat(file)).mode & 0o777, 0o600);
  assert.deepEqual(await fs.readdir(path.dirname(file)), ['huaban-state.json']);
});

test('login credentials cannot be saved inside a repository or through its directory alias', async t => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-state-repo-test-'));
  t.after(() => fs.rm(tmp, { recursive: true, force: true }));
  const repo = path.join(tmp, 'repo');
  await fs.mkdir(path.join(repo, '.git'), { recursive: true });
  await assert.rejects(() => saveState({ cookies: [], origins: [] }, path.join(repo, 'state.json')),
    { message: 'HUABAN_LOGIN_UNSAFE_STATE_PATH' });
  await assert.rejects(fs.access(path.join(repo, 'state.json')));
  await fs.symlink(repo, path.join(tmp, 'alias'), process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(() => saveState({ cookies: [], origins: [] }, path.join(tmp, 'alias', 'state.json')),
    { message: 'HUABAN_LOGIN_UNSAFE_STATE_PATH' });
});
