const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { challengeDetected, desktopAvailability, createHandoff }
  = require('../skills/pod-image-selection/scripts/human_verification.cjs');
const { argumentsFor } = require('../skills/pod-image-selection/scripts/huaban_browser.cjs');

const challenge = { title: '安全验证 - EdgeOne', text: '确认您是真人', cards: 0, search: false,
  hostname: 'huaban.com', frames: [] };
const normal = { title: '狗 动漫 - 花瓣', text: '搜索结果', cards: 3, search: true,
  hostname: 'huaban.com', frames: [] };

test('detects human challenges without treating HTTP errors or search titles as proof', () => {
  assert.equal(challengeDetected(challenge), true);
  assert.equal(challengeDetected({ ...challenge, title: '', text: 'Verify you are human' }), true);
  assert.equal(challengeDetected({ ...normal, frames: ['https://captcha.qq.com/widget'] }), true);
  assert.equal(challengeDetected({ ...normal, title: '人机验证素材搜索', text: '人机验证图片' }), false);
  assert.equal(challengeDetected({ title: '405', text: 'Method not allowed', cards: 0 }), false);
  assert.equal(challengeDetected(normal), false);
});

test('does not call a container, SSH session or xvfb a local interactive desktop', () => {
  const base = { platform: 'linux', exists: () => false };
  assert.equal(desktopAvailability({ ...base, env: { DISPLAY: ':99', XDG_CURRENT_DESKTOP: 'test' },
    run: () => ({ status: 0, stdout: '/usr/bin/Xvfb :99 -screen 0 1600x900x24' }) }).reason, 'virtual_display');
  assert.equal(desktopAvailability({ ...base, env: { DISPLAY: ':0' }, exists: p => p === '/.dockerenv' }).available, false);
  assert.equal(desktopAvailability({ ...base, env: { SSH_CONNECTION: 'test' } }).available, false);
  assert.equal(desktopAvailability({ ...base, env: {} }).reason, 'no_display');
  assert.equal(desktopAvailability({ ...base, env: { DISPLAY: ':0', XDG_CURRENT_DESKTOP: 'GNOME' },
    run: () => ({ status: 0, stdout: '/usr/bin/Xorg :0' }) }).available, true);
  assert.equal(desktopAvailability({ ...base, env: { POD_HUABAN_INTERACTIVE: '1', CI: '1' } }).available, true);
  assert.equal(desktopAvailability({ platform: 'darwin', exists: () => false, env: {} }).available, true);
});

test('handoff controls are bounded while the ordinary user prompt remains unchanged', () => {
  const common = ['--query', '狗 动漫', '--out', '/tmp/result'];
  assert.equal(argumentsFor(common)['human-verification'], 'auto');
  assert.equal(argumentsFor(common)['verification-timeout'], 300);
  assert.throws(() => argumentsFor([...common, '--verification-timeout', '0']));
  assert.throws(() => argumentsFor([...common, '--verification-timeout', '100000']));
  assert.throws(() => argumentsFor([...common, '--human-verification', 'solve']));
});

async function fixture(t, options = {}) {
  const output = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-handoff-'));
  t.after(() => fs.rm(output, { recursive: true, force: true }));
  const events = [], calls = [];
  let closed = false, connected = true;
  const page = { async screenshot() {}, async bringToFront() { calls.push('bringToFront'); },
    async goto() { calls.push('goto'); }, isClosed() { return closed; } };
  const visible = { async newContext(state) {
    calls.push(state.storageState);
    return { async newPage() { return page; } };
  }, isConnected() { return connected; }, async close() { connected = false; } };
  const original = { async close() { calls.push('close_original'); }, isConnected() { return true; } };
  const session = { browser: original, page, context: { async storageState() {
    return { cookies: [{ name: 'fixture-session', value: 'DO_NOT_LOG' }], origins: [] };
  } }, headless: true, contextOptions: { locale: 'zh-CN' } };
  let inspected = 0;
  const handoff = createHandoff({ session, output, onPage() { calls.push('onPage'); },
    inspect: async () => ++inspected === 1 ? challenge : normal,
    desktop: () => ({ available: true, reason: 'fixture' }),
    launch: async () => { calls.push('launch'); return visible; },
    notify: event => events.push(event), timeoutMs: 15, ...options });
  return { handoff, session, calls, events, output,
    closePage() { closed = true; }, async report() { return JSON.parse(await fs.readFile(path.join(output, 'verification.json'))); } };
}

test('a challenge opens one visible session, transfers only in-memory state and resumes', async t => {
  const f = await fixture(t);
  assert.equal(await f.handoff.handle('https://huaban.com/search?q=dog'), true);
  assert.equal(f.session.headless, false);
  assert.equal(f.calls.filter(x => x === 'launch').length, 1);
  assert.ok(f.calls.includes('close_original'));
  assert.equal(f.calls.find(x => typeof x === 'object').cookies[0].value, 'DO_NOT_LOG');
  assert.equal((await f.report()).status, 'completed');
  assert.equal(JSON.stringify(f.events).includes('DO_NOT_LOG'), false);
  assert.deepEqual((await fs.readdir(f.output)).sort(), ['verification.json']);
});

test('remote/xvfb reports a handoff requirement without launching or waiting', async t => {
  const f = await fixture(t, { desktop: () => ({ available: false, reason: 'virtual_display' }) });
  await assert.rejects(() => f.handoff.handle('https://huaban.com/discovery'), { code: 'HUMAN_VERIFICATION_REQUIRED' });
  assert.equal(f.calls.includes('launch'), false);
  assert.equal((await f.report()).status, 'requires_interactive_browser');
});

test('ordinary errors do not trigger a visible-browser retry', async t => {
  const f = await fixture(t, { inspect: async () => ({ title: '403', text: 'Forbidden', cards: 0 }) });
  assert.equal(await f.handoff.handle('https://huaban.com/discovery'), false);
  assert.deepEqual(f.calls, []);
});

test('a challenge rendered just after an HTTP error can be detected without another request', async t => {
  let index = 0;
  const f = await fixture(t, { desktop: () => ({ available: false, reason: 'fixture' }),
    inspect: async () => index++ ? challenge : { title: '', text: '', cards: 0 } });
  await assert.rejects(() => f.handoff.handle('https://huaban.com/discovery', 20), { code: 'HUMAN_VERIFICATION_REQUIRED' });
  assert.equal(f.calls.includes('goto'), false);
});

test('a second challenge stops instead of repeatedly opening windows', async t => {
  let index = 0;
  const f = await fixture(t, { inspect: async () => [challenge, normal, challenge][index++] });
  await f.handoff.handle('https://huaban.com/discovery');
  await assert.rejects(() => f.handoff.handle('https://huaban.com/search?q=dog'), { code: 'HUMAN_VERIFICATION_REPEATED' });
  assert.equal(f.calls.filter(x => x === 'launch').length, 1);
  assert.equal((await f.report()).status, 'blocked_again');
});

test('timeout and user cancellation have distinct outcomes and never reload the challenge', async t => {
  const timed = await fixture(t, { inspect: async () => challenge });
  await assert.rejects(() => timed.handoff.handle('https://huaban.com/discovery'), { code: 'HUMAN_VERIFICATION_TIMEOUT' });
  assert.equal(timed.calls.filter(x => x === 'goto').length, 1);
  assert.equal((await timed.report()).status, 'timed_out');
  const cancelled = await fixture(t, { inspect: async () => challenge });
  cancelled.closePage();
  await assert.rejects(() => cancelled.handoff.handle('https://huaban.com/discovery'), { code: 'HUMAN_VERIFICATION_CANCELLED' });
  assert.equal((await cancelled.report()).status, 'cancelled');
});

test('visible-browser startup failure is actionable and does not expose raw exception data', async t => {
  const f = await fixture(t, { launch: async () => { throw Error('DO_NOT_LOG'); } });
  await assert.rejects(() => f.handoff.handle('https://huaban.com/discovery'), { code: 'HUMAN_VERIFICATION_REQUIRED' });
  const report = await f.report();
  assert.equal(report.status, 'browser_open_failed');
  assert.equal(JSON.stringify(report).includes('DO_NOT_LOG'), false);
});
