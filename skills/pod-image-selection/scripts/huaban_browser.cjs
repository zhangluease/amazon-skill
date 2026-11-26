#!/usr/bin/env node
// Full Playwright collector, separate from the DOM-only Browser Use API.
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { ensureEnvironment } = require('./environment.cjs');
const { closeOwnedBrowser } = require('./browser_lifecycle.cjs');
const { createHandoff } = require('./human_verification.cjs');
const { acquireImageSearchGate } = require('./huaban_image_rate_limit.cjs');
const { validateImageUrl, readReference, submitReference, imageSearchUrl, imageError,
  defaultStateFile } = require('./huaban_image_search.cjs');

const IMAGE_EXTENSIONS = { 'image/webp': '.webp', 'image/png': '.png', 'image/jpeg': '.jpg',
  'image/gif': '.gif', 'image/avif': '.avif' };
const LARGE_VIEWER_IMAGE = '#imageViewerWrapper img[alt="查看图片"]';
const MAX_IMAGE_BYTES = 12 * 1024 * 1024;

function argumentsFor(argv) {
  const result = { similar: [], board: [], pin: [], limit: 40, headed: false };
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i].replace(/^--/, '');
    if (key === 'headed') result.headed = true;
    else if (['query', 'out', 'state', 'limit', 'similar', 'board', 'pin', 'source-file-id', 'browser',
      'human-verification', 'verification-timeout', 'image', 'image-url', 'source-asin'].includes(key)) {
      const value = argv[++i];
      if (!value || value.startsWith('--')) throw Error('参数缺少值');
      if (['similar', 'board', 'pin'].includes(key)) result[key].push(value);
      else result[key] = value;
    } else throw Error('未知参数');
  }
  result.limit = Number(result.limit);
  result['human-verification'] ||= 'auto';
  result['verification-timeout'] = Number(result['verification-timeout'] || 300);
  if (!['auto', 'off'].includes(result['human-verification'])) throw Error('不支持的验证接管模式');
  if (!Number.isInteger(result['verification-timeout']) || result['verification-timeout'] < 30
    || result['verification-timeout'] > 900) throw Error('验证等待时间应为 30–900 秒');
  if (result.browser && !['auto', 'chrome', 'chromium'].includes(result.browser)) throw Error('不支持的浏览器');
  if (!result.out || (!result.query && !result.image && !result['image-url'] && !result.similar.length && !result.board.length && !result.pin.length))
    throw Error('需要 --out 与 --image、--image-url、--query、--similar、--board 或 --pin');
  if ([result.query, result.image, result['image-url']].filter(Boolean).length > 1)
    throw Error('--query、--image 与 --image-url 只能选一个');
  if (result['image-url']) validateImageUrl(result['image-url']);
  if (result['source-asin'] && (!/^[A-Z0-9]{10}$/.test(result['source-asin']) || !result.image && !result['image-url']))
    throw Error('--source-asin 需要图片输入和有效 ASIN');
  if (!Number.isInteger(result.limit) || result.limit < 1 || result.limit > 60)
    throw Error('limit 应为 1–60');
  if ([...result.similar, ...result.board, ...result.pin, ...(result['source-file-id'] ? [result['source-file-id']] : [])]
    .some(id => !/^[1-9]\d*$/.test(id)))
    throw Error('采集和画板 ID 必须是正整数');
  return result;
}

function responsePins(data) {
  if (!data || typeof data !== 'object' || data.error || data.errors) return [];
  const result = [];
  if (Array.isArray(data.pins)) result.push(...data.pins);
  if (data.board && Array.isArray(data.board.pins)) result.push(...data.board.pins);
  for (const board of data.boards || []) {
    for (const pin of board.pins || []) result.push({ ...pin, board_id: board.board_id });
  }
  return result.filter(pin => pin && pin.pin_id && !pin.is_private);
}

function isDataForSource(urlPath, source) {
  if (source.via === 'keyword') return urlPath === '/v3/search/file';
  if (source.via === 'similar')
    return urlPath === `/v3/pins/${source.provenance.parent_pin_id}/similar`;
  if (source.via === 'recommended_board') {
    const base = `/v3/boards/${source.provenance.parent_board_id}`;
    return [base, base + '/pins', base + '/raw_pins'].includes(urlPath);
  }
  return false;
}

async function downloadLargeViewerImage(page, pinUrl, output) {
  const here = new URL(page.url());
  if (here.origin !== 'https://huaban.com' || here.pathname !== new URL(pinUrl).pathname)
    throw imageError('IMAGE_LARGE_VIEW_WRONG_PIN');
  const observed = new Map();
  const keepResponse = response => {
    if (response.request().resourceType() === 'image' && response.ok()) observed.set(response.url(), response);
  };
  page.on('response', keepResponse);
  try {
    await page.getByRole('button', { name: '查看大图', exact: true }).click({ timeout: 5000 });
    const image = await page.waitForFunction(selector => {
      const candidates = Array.from(document.querySelectorAll(selector)).filter(img => img.getClientRects().length);
      if (candidates.length !== 1) return null;
      const img = candidates[0];
      return img.complete && img.naturalWidth && img.naturalHeight && {
        url: img.currentSrc || img.src, width: img.naturalWidth, height: img.naturalHeight
      };
    }, LARGE_VIEWER_IMAGE, { timeout: 7000 });
    const { url, width, height } = await image.jsonValue();
    await image.dispose();
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || !parsed.hostname.endsWith('.huaban.com') || parsed.username || parsed.password || parsed.port)
      throw imageError('IMAGE_LARGE_VIEW_UNSUPPORTED_URL');
    // Prefer the bytes that actually rendered. If preloaded or cached, request
    // the exact viewer URL in this same context without following redirects.
    const captured = observed.get(url);
    const response = captured || await page.request.get(url, {
      headers: { referer: pinUrl, accept: 'image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.8' },
      maxRedirects: 0, timeout: 10000
    });
    let bytes, extension;
    try {
      extension = IMAGE_EXTENSIONS[(response.headers()['content-type'] || '').split(';')[0].trim().toLowerCase()];
      if (!response.ok() || !extension || Number(response.headers()['content-length']) > MAX_IMAGE_BYTES)
        throw imageError('IMAGE_LARGE_VIEW_DOWNLOAD_FAILED');
      bytes = await response.body();
      if (!bytes.length || bytes.length > MAX_IMAGE_BYTES) throw imageError('IMAGE_LARGE_VIEW_DOWNLOAD_FAILED');
      if (!captured) {
        const matches = await page.evaluate(async ({ data, w, h }) => {
          try {
            const bitmap = await createImageBitmap(new Blob([Uint8Array.from(atob(data), c => c.charCodeAt(0))]));
            const equal = bitmap.width === w && bitmap.height === h;
            bitmap.close();
            return equal;
          } catch { return false; }
        }, { data: bytes.toString('base64'), w: width, h: height });
        if (!matches) throw imageError('IMAGE_LARGE_VIEW_DOWNLOAD_MISMATCH');
      }
    } finally {
      if (!captured) await response.dispose();
    }
    const hash = crypto.createHash('sha256').update(bytes).digest('hex');
    const destination = path.join(output, 'browser-assets', hash.slice(0, 20) + extension);
    await fs.writeFile(destination, bytes);
    return { image_url: url, local_image: destination, large_image_status: 'available',
      large_image_width: width, large_image_height: height, large_image_sha256: hash, large_image_pin_url: pinUrl };
  } finally {
    page.off('response', keepResponse);
  }
}

async function collect(options, dependencies = {}) {
  const output = path.resolve(options.out);
  const started = Date.now();
  const environment = await (dependencies.prepare || ensureEnvironment)({ out: output, prepare: true, keepBrowser: true,
    headless: !options.headed, browserPreference: options.browser || 'auto' });
  if (!environment.report.ready) throw Object.assign(Error('环境未就绪'), {
    code: 'ENVIRONMENT_NOT_READY', report: environment.report });
  const browser = environment.browser;
  const session = { browser, headless: !options.headed };
  let page;
  let handoff;
  let imageGate;
  try {
    await fs.mkdir(path.join(output, 'browser-assets'), { recursive: true });
    // Preserve the installed Chrome version and normal Chrome UA in both modes.
    // No stealth package, webdriver patches, browser profile or credential DB access.
    const probe = await browser.newPage();
    const userAgent = (await probe.evaluate(() => navigator.userAgent))
      .replace('HeadlessChrome/', 'Chrome/');
    await probe.close();
    let storageState;
    const savedState = defaultStateFile();
    const stateFile = options.state || (await fs.access(savedState).then(() => savedState).catch(() => null));
    if (stateFile) {
      const supplied = JSON.parse(await fs.readFile(path.resolve(stateFile), 'utf8'));
      storageState = {
        cookies: (supplied.cookies || []).filter(c => c.domain.replace(/^\./, '') === 'huaban.com'),
        origins: (supplied.origins || []).filter(o => o.origin === 'https://huaban.com')
      };
    }
    const contextOptions = { userAgent, storageState,
      locale: 'zh-CN', viewport: { width: 1600, height: 1800 } };
    const context = await browser.newContext(contextOptions);
    page = await context.newPage();
    Object.assign(session, { context, page, contextOptions });
    const documents = [], events = [], jobs = [];
    let active = null;
    const observePage = observed => observed.on('response', response => {
      const source = active;
      if (!source) return;
      const url = new URL(response.url());
      const isData = url.hostname === 'huaban.com' && isDataForSource(url.pathname, source);
      if (isData) jobs.push((async () => {
        events.push({ via: source.via, path: url.pathname, status: response.status() });
        try {
          const pins = responsePins(await response.json());
          for (const pin of pins) source.pins.push({ ...pin, source_paths: [source.provenance] });
        } catch { /* HTML and error bodies are not candidate data. */ }
      })());
    });
    observePage(page);
    handoff = createHandoff({ session, output, onPage: observePage,
      mode: options['human-verification'] || 'auto', timeoutMs: (options['verification-timeout'] || 300) * 1000,
      desktop: dependencies.desktop, notify: dependencies.notify,
      launch: () => environment.playwright.chromium.launch({
        ...(environment.report.checks.browser.engine === 'chrome' ? { channel: 'chrome' } : {}),
        headless: false, timeout: 12000 }) });
    // Replace a prior run's handoff status when the same output directory is reused.
    await fs.writeFile(path.join(output, 'verification.json'), JSON.stringify(handoff.report, null, 2) + '\n');
    const handleVerification = async (target, detectTimeoutMs = 0) => {
      const handled = await handoff.handle(target, detectTimeoutMs);
      page = session.page;
      if (handled && page.url() !== target) {
        const response = await page.goto(target, { waitUntil: 'load', timeout: 25000 });
        await handoff.handle(target); // A second challenge stops, rather than opening another window.
        if (!response?.ok()) throw Error('目标页 HTTP ' + response?.status() + '，停止本轮请求');
      }
      return handled;
    };
    const navigate = async target => {
      let response;
      try { response = await page.goto(target, { waitUntil: 'load', timeout: 25000 }); }
      catch (error) { if (await handleVerification(target)) return; throw error; }
      // Error pages may render their challenge after load. Only observe this document briefly.
      if (await handleVerification(target, response?.ok() ? 0 : 1500)) return;
      if (!response?.ok()) throw Error('页面 HTTP ' + response?.status() + '，停止本轮请求');
    };
    if (options.query || options.image || options['image-url'] || options.similar.length || options.board.length)
      await navigate('https://huaban.com/discovery');
    const targets = [];
    if (options.image || options['image-url']) {
      const reference = await readReference(options, dependencies);
      const target = { via: 'image_search', pins: [], provenance: reference.provenance };
      imageGate = await acquireImageSearchGate(dependencies.rateLimitDirectory);
      // Candidate DOM is read only after /similar is confirmed.
      active = target;
      try { target.url = await submitReference(page, reference); }
      catch (error) {
        const current = page.url();
        // A challenge can resume at the actual result URL without repeating the upload.
        if (!imageSearchUrl(current) || !await handleVerification(current)) throw error;
        target.url = current;
      }
      target.provenance.search_url = target.url;
      await handleVerification(target.url);
      targets.push(target);
      active = null;
    }
    if (options.query) targets.push({ via: 'keyword', url: 'https://huaban.com/search?q=' +
      encodeURIComponent(options.query), provenance: { via: 'keyword', query: options.query } });
    for (const id of options.similar) targets.push({ via: 'similar',
      url: `https://huaban.com/pins/${id}/similar`, provenance: { via: 'similar', parent_pin_id: id } });
    for (const id of options.board) targets.push({ via: 'recommended_board',
      url: `https://huaban.com/boards/${id}` + (options['source-file-id'] ?
        `?source_file_id=${options['source-file-id']}` : ''),
      provenance: { via: 'recommended_board', parent_board_id: id,
        ...(options['source-file-id'] ? { source_file_id: options['source-file-id'] } : {}) } });
    for (const target of targets) {
      active = { ...target, pins: target.pins || [] };
      if (target.via !== 'image_search') await navigate(target.url);
      const waitForCards = async () => {
        try {
          await page.locator('a[href*="/pins/"] img').first().waitFor({ state: 'visible', timeout: 10000 });
          await page.waitForFunction(minimum => [...document.querySelectorAll('a[href*="/pins/"] img')]
            .filter(img => img.complete && img.naturalWidth).length >= minimum,
          Math.min(8, options.limit), { timeout: 8000 });
        } catch { /* Check for a late challenge before treating the page as empty. */ }
      };
      await waitForCards();
      if (await handleVerification(target.url)) await waitForCards();
      if (target.via === 'image_search') {
        const actual = new URL(page.url()), expected = new URL(target.url);
        if (!imageSearchUrl(actual.href) || ['bucket', 'key'].some(key =>
          actual.searchParams.get(key) !== expected.searchParams.get(key)))
          throw imageError('IMAGE_SEARCH_NOT_CONFIRMED');
      }
      await page.screenshot({ path: path.join(output, `overview-${documents.length + 1}.jpg`) });
      const dom = await page.locator('a[href*="/pins/"] img').evaluateAll(elements => elements.map(img => {
        const id = img.closest('a')?.getAttribute('href')?.match(/\/pins\/(\d+)/)?.[1];
        return { pin_id: id, title: img.alt || img.title || '', image_url: img.currentSrc || img.src,
          width: null, height: null, dimension_source: 'dom_thumbnail',
          thumbnail_width: img.naturalWidth || null, thumbnail_height: img.naturalHeight || null };
      }).filter(pin => pin.pin_id));
      await Promise.allSettled(jobs);
      const api = new Map(active.pins.map(pin => [String(pin.pin_id), pin]));
      for (const pin of dom) {
        const prior = api.get(String(pin.pin_id));
        if (prior) Object.assign(prior, { image_url: pin.image_url,
          thumbnail_width: pin.thumbnail_width, thumbnail_height: pin.thumbnail_height });
        else api.set(String(pin.pin_id), { ...pin, source_paths: [active.provenance] });
      }
      // Match the rendered ordering (including source-file similarity sorting),
      // rather than truncating an earlier API's chronological board list first.
      const ordered = new Map();
      for (const pin of dom) ordered.set(String(pin.pin_id), api.get(String(pin.pin_id)));
      for (const [id, pin] of api) if (!ordered.has(id)) ordered.set(id, pin);
      documents.push({ ...target, pins: [...ordered.values()].slice(0, options.limit) });
      if (target.via === 'image_search') await imageGate.complete();
      active = null;
    }
    if (options.pin?.length) documents.push({ via: 'pin_detail', pins: Array.from(new Set(options.pin))
      .slice(0, options.limit).map(pin_id => ({ pin_id, source_paths: [{ via: 'pin_detail' }] })) });
    let cached = 0;
    let siteBlocked = false;
    const byPin = new Map();
    for (const document of documents) for (const pin of document.pins) {
      const id = String(pin.pin_id);
      if (!/^[1-9]\d*$/.test(id)) continue;
      pin.pin_url = 'https://huaban.com/pins/' + id;
      pin.image_url = null; // Listing thumbnails are for candidate discovery only.
      delete pin.local_image;
      if (!byPin.has(id)) {
        const unavailable = { large_image_status: 'unavailable', large_image_error: 'IMAGE_LARGE_VIEW_UNAVAILABLE' };
        if (siteBlocked) unavailable.large_image_error = 'IMAGE_LARGE_VIEW_ACCESS_BLOCKED';
        else {
          try {
            await navigate(pin.pin_url);
            const asset = await downloadLargeViewerImage(page, pin.pin_url, output);
            if (!pin.title && !pin.raw_text) asset.title = await page.title();
            byPin.set(id, asset);
          } catch (error) {
            if (/^HUMAN_VERIFICATION_/.test(error.code || '')) throw error;
            siteBlocked = /HTTP (401|403|429)\b/.test(error.message);
            if (siteBlocked) unavailable.large_image_error = 'IMAGE_LARGE_VIEW_ACCESS_BLOCKED';
            else if (/^IMAGE_LARGE_VIEW_[A-Z_]+$/.test(error.code || '')) unavailable.large_image_error = error.code;
          }
        }
        if (!byPin.has(id)) byPin.set(id, unavailable);
      }
      Object.assign(pin, byPin.get(id));
      if (pin.large_image_status === 'available') cached++;
    }
    const summary = { headless: session.headless, supplied_state: Boolean(options.state),
      huaban_session_source: options.state ? 'explicit' : stateFile ? 'saved' : 'anonymous',
      browser_engine: environment.report.checks.browser.engine,
      human_verification: handoff.report,
      normal_chrome_ua: true, elapsed_ms: Date.now() - started,
      search_mode: options.image || options['image-url'] ? 'image_search' : options.query ? 'keyword'
        : options.similar.length || options.board.length ? 'expansion' : 'pin_details',
      ...(imageGate ? { image_search_rate_limit: imageGate.timing } : {}),
      candidates: documents.reduce((sum, d) => sum + d.pins.length, 0), cached,
      large_image_unavailable: [...byPin.values()].filter(pin => pin.large_image_status === 'unavailable').length, events };
    await fs.writeFile(path.join(output, 'browser-candidates.json'), JSON.stringify({ documents, summary }, null, 2));
    await fs.writeFile(path.join(output, 'response.json'), JSON.stringify({ pins: documents.flatMap(d => d.pins) }, null, 2));
    await fs.writeFile(path.join(output, 'timing.json'), JSON.stringify(summary, null, 2));
    if (!summary.candidates) throw Error('没有取得候选；请查看截图，区分登录要求、空结果和访问限制');
    if (!cached) throw imageError('IMAGE_LARGE_VIEW_UNAVAILABLE');
    if (!options.quiet) console.log(JSON.stringify(summary));
    return summary;
  } catch (error) {
    page = session.page || page;
    if (page) await page.screenshot({ path: path.join(output, 'failure.jpg') }).catch(() => {});
    const status = String(error.message).match(/HTTP (\d{3})/)?.[1];
    await fs.writeFile(path.join(output, 'failure.json'), JSON.stringify({
      status: status ? Number(status) : null,
      category: /^HUMAN_VERIFICATION_/.test(error.code || '') ? error.code.toLowerCase()
        : /^IMAGE_(?:REFERENCE|SEARCH|LARGE_VIEW)_/.test(error.code || '') ? error.code.toLowerCase()
        : status ? 'access_blocked' : 'collection_failed',
      ...(error.code === 'IMAGE_SEARCH_LOGIN_REQUIRED' ? {
        remedy: '花瓣以图搜图需要登录。请通过--state提供用户授权的花瓣Playwright storageState会话文件，再运行同一图片。'
      } : {}),
      ...(error.verification ? { verification: error.verification } : {}),
      elapsed_ms: Date.now() - started
    }, null, 2));
    throw error;
  } finally {
    try { await imageGate?.release(); }
    finally { await closeOwnedBrowser(session.browser).catch(() => {}); }
  }
}

if (require.main === module) Promise.resolve().then(() => collect(argumentsFor(process.argv.slice(2)))).catch(error => {
  if (error.code === 'ENVIRONMENT_NOT_READY') {
    console.error(JSON.stringify({ error: error.code, ...error.report }));
    process.exitCode = 2;
    return;
  }
  if (/^HUMAN_VERIFICATION_/.test(error.code || '')) {
    console.error(JSON.stringify({ error: error.code, verification: error.verification }));
    process.exitCode = 3;
    return;
  }
  if (error.code === 'IMAGE_SEARCH_LOGIN_REQUIRED') {
    console.error('花瓣以图搜图需要登录；请通过 --state 提供有效的花瓣会话文件。本轮未取得以图搜索结果。');
    process.exitCode = 3;
    return;
  }
  if (error.code === 'IMAGE_LARGE_VIEW_UNAVAILABLE') {
    console.error('未取得候选的「查看大图」文件；请查看 response.json 中的状态。');
    process.exitCode = 3;
    return;
  }
  // Do not echo server content, storageState, cookies, or credentials on failures.
  console.error('浏览器采集未成功；检查依赖、参数和输出截图。不会自动重试或尝试登录。');
  process.exitCode = 2;
});
module.exports = { argumentsFor, responsePins, isDataForSource, collect };
