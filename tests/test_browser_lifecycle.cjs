const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { closeOwnedBrowser } = require('../skills/pod-image-selection/scripts/browser_lifecycle.cjs');
const { findPlaywright } = require('../skills/pod-image-selection/scripts/environment.cjs');

test('waits for every context to finish before closing the owned browser', async () => {
  const events = [];
  let finish;
  const closing = closeOwnedBrowser({
    contexts: () => [{ async close() {
      events.push('context-start');
      await new Promise(resolve => { finish = resolve; });
      events.push('context-finished');
    } }, { async close() { events.push('other-context-finished'); } }],
    async close() { events.push('browser-finished'); }
  });
  assert.deepEqual(events, ['context-start']);
  finish();
  await closing;
  assert.deepEqual(events, ['context-start', 'context-finished', 'other-context-finished', 'browser-finished']);
});

test('a failing context does not leak the other contexts or the owned browser', async () => {
  const failure = Error('context failed'), events = [];
  await assert.rejects(closeOwnedBrowser({
    contexts: () => [{ async close() { throw failure; } },
      { async close() { events.push('other-context-finished'); } }],
    async close() { events.push('browser-finished'); }
  }), error => error === failure);
  assert.deepEqual(events, ['other-context-finished', 'browser-finished']);
});

const runtime = findPlaywright();
test('real contexts close before disconnect and an already closed browser can be cleaned again',
  { skip: !runtime.playwright, timeout: 20000 }, async () => {
    const browser = await runtime.playwright.chromium.launch({ headless: true });
    try {
      const events = [];
      browser.on('disconnected', () => events.push('browser'));
      for (let i = 0; i < 2; i++) {
        const context = await browser.newContext();
        context.on('close', () => events.push('context-' + i));
        await (await context.newPage()).setContent('<title>local shutdown fixture</title>');
      }
      await closeOwnedBrowser(browser);
      assert.deepEqual(events, ['context-0', 'context-1', 'browser']);
      assert.equal(browser.isConnected(), false);
      await closeOwnedBrowser(browser);
    } finally { await closeOwnedBrowser(browser); }
  });

test('a disposable persistent profile records a clean exit and reopens successfully',
  { skip: !runtime.playwright, timeout: 20000 }, async () => {
    const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'pod-clean-exit-'));
    let context;
    try {
      context = await runtime.playwright.chromium.launchPersistentContext(profile, { channel: 'chromium', headless: true });
      await context.pages()[0].setContent('<title>local profile fixture</title>');
      await closeOwnedBrowser(context.browser());
      const preferences = JSON.parse(await fs.readFile(path.join(profile, 'Default', 'Preferences'), 'utf8'));
      assert.equal(preferences.profile.exit_type, 'Normal');
      context = await runtime.playwright.chromium.launchPersistentContext(profile, { channel: 'chromium', headless: true });
      await context.pages()[0].setContent('<title>reopened fixture</title>');
      assert.equal(await context.pages()[0].title(), 'reopened fixture');
      await context.close();
    } finally {
      await context?.close().catch(() => {});
      await fs.rm(profile, { recursive: true, force: true });
    }
  });
