// Human-only verification handoff. Never clicks or solves a challenge.
const fs = require('node:fs/promises');
const syncFs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');

const HUMAN = /确认.{0,4}(?:您|你).{0,4}真人|验证.{0,4}(?:您|你).{0,4}(?:真人|人类)|人机验证|请完成.{0,8}验证|verify.{0,30}(?:human|not a robot)|confirm.{0,20}human|checking your browser|security verification|captcha/i;
const SECURITY = /安全验证|访问验证|访问校验|安全检查|security check/i;

function challengeDetected(snapshot) {
  const frame = (snapshot.frames || []).some(url =>
    /(?:captcha\.qq\.com|turing\.captcha|challenges\.cloudflare\.com|hcaptcha\.com|recaptcha)/i.test(url));
  if (frame) return true;
  if (!snapshot.cards && !snapshot.search
    && (HUMAN.test(snapshot.title || '') || SECURITY.test(snapshot.title || ''))) return true;
  // A search query, image title or footer mentioning CAPTCHA is not a challenge page.
  return !snapshot.cards && !snapshot.search && String(snapshot.text || '').length < 6000
    && (HUMAN.test(snapshot.text || '') || SECURITY.test(snapshot.text || '')
      && /edgeone|验证码|腾讯云/i.test(snapshot.text || ''));
}

function desktopAvailability({ platform = process.platform, env = process.env,
  exists = syncFs.existsSync, run = spawnSync } = {}) {
  if (env.POD_HUABAN_INTERACTIVE === '0') return { available: false, reason: 'disabled' };
  if (env.POD_HUABAN_INTERACTIVE === '1') return { available: true, reason: 'confirmed_interactive_desktop' };
  if (env.SSH_CONNECTION || env.SSH_TTY || env.CI && !/^(?:0|false)$/i.test(env.CI)
    || env.KUBERNETES_SERVICE_HOST || env.CODESPACES || env.REMOTE_CONTAINERS
    || exists('/.dockerenv') || exists('/run/.containerenv'))
    return { available: false, reason: 'remote_or_container' };
  if (platform === 'win32' && /^services$/i.test(env.SESSIONNAME || ''))
    return { available: false, reason: 'noninteractive_session' };
  if (platform === 'darwin' || platform === 'win32') return { available: true, reason: 'local_desktop' };
  if (platform === 'linux') {
    if (!env.DISPLAY && !env.WAYLAND_DISPLAY) return { available: false, reason: 'no_display' };
    const result = run('ps', ['-eo', 'args='], { encoding: 'utf8', timeout: 3000 });
    if (result.status !== 0) return { available: false, reason: 'display_unconfirmed' };
    if (/^\s*(?:\S*\/)?(?:Xvfb|Xdummy)(?:\s|$)/mi.test(result.stdout || ''))
      return { available: false, reason: 'virtual_display' };
    if (env.XDG_CURRENT_DESKTOP || env.DESKTOP_SESSION)
      return { available: true, reason: 'desktop_session' };
    return { available: false, reason: 'display_unconfirmed' };
  }
  return { available: false, reason: 'unsupported_desktop' };
}

async function pageSnapshot(page) {
  const snapshot = await page.evaluate(() => ({
    title: document.title, text: (document.body?.innerText || '').slice(0, 6500),
    cards: document.querySelectorAll('a[href*="/pins/"] img').length,
    search: Boolean(document.querySelector('input[type="search"], input[placeholder*="搜索"], input[aria-label*="搜索"]')),
    siteLinks: document.querySelectorAll('a[href="/discovery"], a[href^="/search"], a[href^="/boards/"]').length,
    hostname: location.hostname,
    frames: [...document.querySelectorAll('iframe')].filter(frame => frame.getClientRects().length
      && getComputedStyle(frame).visibility !== 'hidden').map(frame => frame.src)
  }));
  return snapshot;
}

function handoffError(code, message, report) {
  return Object.assign(Error(message), { code, verification: report });
}

function createHandoff({ session, output, onPage, mode = 'auto', timeoutMs = 300000,
  desktop = desktopAvailability, inspect = pageSnapshot, launch,
  notify = value => process.stderr.write(JSON.stringify(value) + '\n') }) {
  let used = false;
  let report = { status: 'not_needed' };
  const reportPath = path.join(output, 'verification.json');
  async function publish(values) {
    report = { ...report, ...values, report_path: reportPath };
    await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
    notify({ event: 'human_verification', ...report });
  }
  async function handle(target, detectTimeoutMs = 0) {
    let snapshot;
    const detectUntil = Date.now() + detectTimeoutMs;
    for (;;) {
      try { snapshot = await inspect(session.page); } catch { snapshot = null; }
      if (snapshot && challengeDetected(snapshot)) break;
      if (Date.now() >= detectUntil) return false;
      await delay(Math.min(100, detectUntil - Date.now()));
    }
    await session.page.screenshot({ path: path.join(output, 'verification.jpg') }).catch(() => {});
    const access = desktop();
    if (mode === 'off' || !access.available) {
      await publish({ status: 'requires_interactive_browser', desktop: access,
        message: mode === 'off' ? '检测到真人验证；当前命令关闭了交互接管。'
          : '检测到真人验证，但当前环境无法确认用户能操作浏览器。请在本机执行，或使用平台提供的可交互远程浏览器；xvfb 不等于用户接管入口。' });
      throw handoffError('HUMAN_VERIFICATION_REQUIRED', report.message, report);
    }
    if (used) {
      await publish({ status: 'blocked_again', message: '本轮已完成一次接管，网站再次要求验证；停止本轮采集，不循环弹窗。' });
      throw handoffError('HUMAN_VERIFICATION_REPEATED', report.message, report);
    }
    used = true;
    if (session.headless) {
      let visible;
      try {
        // Transfer only this run's isolated context in memory. No daily browser profile or state file.
        const storageState = await session.context.storageState();
        visible = await launch();
        const context = await visible.newContext({ ...session.contextOptions, storageState });
        const page = await context.newPage();
        const previous = session.browser;
        Object.assign(session, { browser: visible, context, page, headless: false });
        onPage(page);
        await previous.close();
      } catch {
        if (visible && visible !== session.browser) await visible.close().catch(() => {});
        await publish({ status: 'browser_open_failed', desktop: access,
          message: '无法打开可见浏览器。请检查本机桌面或使用平台的远程浏览器接管入口。' });
        throw handoffError('HUMAN_VERIFICATION_REQUIRED', report.message, report);
      }
      await publish({ status: 'opening_browser', desktop: access,
        message: '正在打开验证页面；请准备在可见浏览器中手动完成验证。' });
      // A single navigation in the new visible session; never retries challenge requests in a loop.
      try { await session.page.goto(target, { waitUntil: 'domcontentloaded', timeout: 25000 }); }
      catch {
        await publish({ status: 'navigation_failed', message: '可见浏览器未能加载验证页面，请检查网络后重新发起任务。' });
        throw handoffError('HUMAN_VERIFICATION_REQUIRED', report.message, report);
      }
    }
    await session.page.bringToFront();
    await publish({ status: 'awaiting_human', desktop: access, timeout_seconds: timeoutMs / 1000,
      message: '请在打开的浏览器中手动完成真人验证，保持窗口打开；通过后会自动继续采集。' });
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (session.page.isClosed() || !session.browser.isConnected()) {
        await publish({ status: 'cancelled', message: '验证窗口已关闭，本轮采集停止。' });
        throw handoffError('HUMAN_VERIFICATION_CANCELLED', report.message, report);
      }
      try {
        const state = await inspect(session.page);
        if (state.hostname === 'huaban.com' && !challengeDetected(state)
          && (state.cards || state.search || state.siteLinks)) {
          await publish({ status: 'completed', message: '已检测到正常花瓣页面，正在沿用当前可见会话继续采集。' });
          return true;
        }
      } catch { /* A user-triggered redirect may briefly destroy the document. */ }
      // Only observes the existing page. No reloads, challenge clicks or network probes.
      await delay(Math.min(500, Math.max(0, deadline - Date.now())));
    }
    await publish({ status: 'timed_out', message: '等待真人验证超时，本轮采集停止，浏览器将关闭。' });
    throw handoffError('HUMAN_VERIFICATION_TIMEOUT', report.message, report);
  }
  return { handle, get report() { return report; } };
}

module.exports = { challengeDetected, desktopAvailability, pageSnapshot, createHandoff };
