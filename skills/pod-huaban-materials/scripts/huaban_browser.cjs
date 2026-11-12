#!/usr/bin/env node
// Full Playwright collector, separate from the DOM-only Browser Use API.
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

function argumentsFor(argv) {
  const result = { similar: [], board: [], limit: 40, headed: false };
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i].replace(/^--/, '');
    if (key === 'headed') result.headed = true;
    else if (['query', 'out', 'state', 'limit', 'similar', 'board', 'source-file-id'].includes(key)) {
      const value = argv[++i];
      if (!value || value.startsWith('--')) throw Error('参数缺少值');
      if (key === 'similar' || key === 'board') result[key].push(value);
      else result[key] = value;
    } else throw Error('未知参数');
  }
  result.limit = Number(result.limit);
  if (!result.out || (!result.query && !result.similar.length && !result.board.length))
    throw Error('需要 --out 与 --query、--similar 或 --board');
  if (!Number.isInteger(result.limit) || result.limit < 1 || result.limit > 60)
    throw Error('limit 应为 1–60');
  if ([...result.similar, ...result.board, ...(result['source-file-id'] ? [result['source-file-id']] : [])]
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

async function collect(options) {
  let chromium;
  try { ({ chromium } = require('playwright')); }
  catch { throw Error('缺少 playwright；先安装 Node.js 和 playwright 包及 Chrome'); }
  const output = path.resolve(options.out);
  await fs.mkdir(path.join(output, 'browser-assets'), { recursive: true });
  const started = Date.now();
  const browser = await chromium.launch({ channel: 'chrome', headless: !options.headed });
  let page;
  try {
    // Preserve the installed Chrome version and normal Chrome UA in both modes.
    // No stealth package, webdriver patches, browser profile or credential DB access.
    const probe = await browser.newPage();
    const userAgent = (await probe.evaluate(() => navigator.userAgent))
      .replace('HeadlessChrome/', 'Chrome/');
    await probe.close();
    let storageState;
    if (options.state) {
      const supplied = JSON.parse(await fs.readFile(path.resolve(options.state), 'utf8'));
      storageState = {
        cookies: (supplied.cookies || []).filter(c => c.domain.replace(/^\./, '') === 'huaban.com'),
        origins: (supplied.origins || []).filter(o => o.origin === 'https://huaban.com')
      };
    }
    const context = await browser.newContext({ userAgent, storageState,
      locale: 'zh-CN', viewport: { width: 1600, height: 1800 } });
    page = await context.newPage();
    const documents = [], events = [], images = new Map(), jobs = [];
    let active = null;
    page.on('response', response => {
      const source = active;
      if (!source) return;
      const url = new URL(response.url()), type = response.headers()['content-type'] || '';
      const isData = url.hostname === 'huaban.com' && isDataForSource(url.pathname, source);
      if (isData) jobs.push((async () => {
        events.push({ via: source.via, path: url.pathname, status: response.status() });
        try {
          const pins = responsePins(await response.json());
          for (const pin of pins) source.pins.push({ ...pin, source_paths: [source.provenance] });
        } catch { /* HTML and error bodies are not candidate data. */ }
      })());
      if (url.hostname.endsWith('.huaban.com') && type.startsWith('image/') && response.ok())
        jobs.push((async () => {
          try {
            const body = await response.body();
            if (body.length <= 12 * 1024 * 1024 && images.size < 300)
              images.set(response.url(), { body, type: type.split(';')[0] });
          } catch { /* A candidate without an image stays explicitly incomplete. */ }
        })());
    });
    const warmup = await page.goto('https://huaban.com/discovery', {
      waitUntil: 'load', timeout: 25000 });
    if (!warmup.ok()) throw Error('发现页 HTTP ' + warmup.status() + '，停止本轮请求');
    const targets = [];
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
      active = { ...target, pins: [] };
      const response = await page.goto(target.url, { waitUntil: 'load', timeout: 25000 });
      if (!response.ok()) throw Error('目标页 HTTP ' + response.status() + '，停止本轮请求');
      try {
        await page.locator('a[href*="/pins/"] img').first().waitFor({ state: 'visible', timeout: 10000 });
        await page.waitForFunction(() => [...document.querySelectorAll('a[href*="/pins/"] img')]
          .filter(img => img.complete && img.naturalWidth).length >= 8, null, { timeout: 8000 });
      } catch { /* Verify candidate and image counts below, including empty pages. */ }
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
      active = null;
    }
    const extensions = { 'image/webp': '.webp', 'image/png': '.png', 'image/jpeg': '.jpg',
      'image/gif': '.gif', 'image/avif': '.avif' };
    let cached = 0;
    for (const document of documents) for (const pin of document.pins) {
      pin.pin_url = 'https://huaban.com/pins/' + pin.pin_id;
      const key = pin.file?.key || new URL(pin.image_url || pin.file?.url || 'https://huaban.com/')
        .pathname.split('/').pop().replace(/_(?:fw|sq)\d+(?:webp)?$/, '');
      const found = [...images].filter(([url, value]) => key && new URL(url).pathname.includes(key)
        && extensions[value.type]);
      found.sort((a, b) => (Number(b[0].match(/_fw(\d+)/)?.[1]) || 0) -
        (Number(a[0].match(/_fw(\d+)/)?.[1]) || 0));
      if (found.length) {
        const [url, asset] = found[0];
        const name = crypto.createHash('sha256').update(url).digest('hex').slice(0, 20) + extensions[asset.type];
        const destination = path.join(output, 'browser-assets', name);
        await fs.writeFile(destination, asset.body);
        Object.assign(pin, { local_image: destination, image_url: url });
        cached++;
      }
    }
    const summary = { headless: !options.headed, supplied_state: Boolean(options.state),
      normal_chrome_ua: true, elapsed_ms: Date.now() - started,
      candidates: documents.reduce((sum, d) => sum + d.pins.length, 0), cached, events };
    await fs.writeFile(path.join(output, 'browser-candidates.json'), JSON.stringify({ documents, summary }, null, 2));
    await fs.writeFile(path.join(output, 'response.json'), JSON.stringify({ pins: documents.flatMap(d => d.pins) }, null, 2));
    await fs.writeFile(path.join(output, 'timing.json'), JSON.stringify(summary, null, 2));
    if (!summary.candidates) throw Error('没有取得候选；请查看截图，区分登录要求、空结果和访问限制');
    console.log(JSON.stringify(summary));
    return summary;
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(output, 'failure.jpg') }).catch(() => {});
    const status = String(error.message).match(/HTTP (\d{3})/)?.[1];
    await fs.writeFile(path.join(output, 'failure.json'), JSON.stringify({
      status: status ? Number(status) : null, category: status ? 'access_blocked' : 'collection_failed',
      elapsed_ms: Date.now() - started
    }, null, 2));
    throw error;
  } finally { await browser.close(); }
}

if (require.main === module) Promise.resolve().then(() => collect(argumentsFor(process.argv.slice(2)))).catch(() => {
  // Do not echo server content, storageState, cookies, or credentials on failures.
  console.error('浏览器采集未成功；检查依赖、参数和输出截图。不会自动重试或尝试登录。');
  process.exitCode = 2;
});
module.exports = { argumentsFor, responsePins, isDataForSource, collect };
