// Read-only local checks and actionable public errors; never return session headers.
const fs = require('node:fs/promises');
const path = require('node:path');
const { findPython, inspectRuntime, desktopAvailability } = require('./environment.cjs');
const { discoverExtension, readManifest, extensionId, stateRoot } = require('./sellersprite_session.cjs');
const { sessionFromBridge } = require('./sellersprite_auth.cjs');

const GUIDANCE = {
  NODE_18_REQUIRED: ['需要 Node.js 18 或更新版本。', '安装 Node.js 后重新运行 check。'],
  PYTHON_39_REQUIRED: ['未找到 Python 3.9 或更新版本。', '安装 Python，或用 AMAZON_COMPETITOR_PYTHON 指定解释器。'],
  EXTENSION_NOT_FOUND: ['未找到卖家精灵原扩展。', '在 Chrome／Edge 安装原扩展，或用 --extension-dir 指定扩展程序目录。'],
  INVALID_EXTENSION_DIRECTORY: ['指定目录不是可用的卖家精灵原扩展。', '将 --extension-dir 指向包含 manifest.json 和 popup 页的程序目录。'],
  EXTENSION_ID_UNAVAILABLE: ['扩展未提供授权桥接所需的标识。', '安装 Chrome 商店的原扩展后重试。'],
  INTERACTIVE_BROWSER_REQUIRED: ['需要可见桌面完成扫码登录。', '在本地桌面运行；确认桌面可用后可设置 AMAZON_COMPETITOR_INTERACTIVE=1。'],
  NPM_REQUIRED: ['未找到用于安装登录依赖的 npm。', '安装包含 npm 的 Node.js，然后运行 prepare。'],
  PLAYWRIGHT_MISSING: ['登录所需的 Playwright 尚未准备。', '运行 prepare；有效 HTTP 会话查询不需要它。'],
  PLAYWRIGHT_INSTALL_FAILED: ['登录依赖下载或安装失败。', '检查 npm 网络或代理设置，再运行 prepare。'],
  BROWSER_INSTALL_FAILED: ['登录浏览器下载失败。', '检查下载网络或代理设置，再运行 prepare。'],
  BROWSER_LAUNCH_FAILED: ['专用登录浏览器无法启动。', '运行 prepare 检查浏览器；Linux 还需安装 Chromium 系统依赖。'],
  BROWSER_CONFIG_INVALID: ['浏览器缓存配置无法读取。', '检查 ~/.amazon-skill/config.json 中 browser.cache_dir，要求是本机绝对路径。'],
  PROFILE_BUSY: ['另一个任务正在使用同一专用会话。', '等待该任务结束，或用独立 --state-dir 运行另一个账号。'],
  AUTHORIZATION_TIMEOUT: ['扫码登录等待超时。', '用新报告目录重试，并在弹出的窗口中完成扫码。'],
  AUTHORIZATION_CANCELLED: ['扫码登录窗口已关闭。', '用新报告目录重试，授权完成前保持窗口打开。'],
  AUTHORIZATION_REJECTED: ['卖家精灵未接受本次授权。', '核对自己的账号登录和权限，用新报告目录重试。'],
  PLUGIN_LOGIN_REQUIRED: ['卖家精灵返回星号登录遮罩，当前数据尚未解锁。', '完成本轮浏览器登录后重查；遮罩不写入表格，也不代表没有图片变化。'],
  OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY: ['输出目录已有报告或采集证据。', '每次查询选择新的 --out 目录。'],
  ERR_GLOBAL_403: ['卖家精灵拒绝了这个接口请求。', '检查当前账号的数据权限或扩展版本；本轮不会重复弹登录窗口。'],
  ERR_ROBOT_CHECK: ['卖家精灵要求人机验证，已保留本轮取得的数据。', '在原扩展中完成服务端要求的验证后重试；脚本不会自动处理验证码或反复续登。'],
  HTTP_403: ['数据接口拒绝访问。', '检查账号权限或访问状态，已有结果仍保留。'],
  HTTP_429: ['请求受到限流。', '稍后用新目录查询，可增大 --interval。'],
  ERR_FREQUENT: ['卖家精灵提示请求过于频繁。', '稍后重试，可增大 --interval。'],
  ERR_VERSION_NEED_UPGRADE: ['卖家精灵要求升级扩展。', '升级本机原扩展后重试。'],
  ASIN_NOT_RETURNED: ['接口没有返回这个 ASIN。', '核对日本站 ASIN；未确认的商品不会标记为无修改。'],
  PRODUCT_NOT_CACHED: ['卖家精灵暂未缓存这个商品。', '确认商品可访问，稍后再查；当前缺项保留为未知。'],
  INVALID_JP_PRODUCT_URL_OR_ASIN: ['ASIN 或日本站商品链接无效。', '提供 10 位 ASIN，或 https://www.amazon.co.jp/dp/ASIN。'],
  NETWORK_FAILED: ['接口网络请求失败。', '检查网络或代理连接后，用新报告目录重试。'],
};

function guidance(code) {
  const [message, next_step] = GUIDANCE[code] || ['本次操作未完成，已保留可用结果。', '根据 category 和报告中的 errors 定位失败来源。'];
  return { message, next_step };
}

async function check(options, sessionFile, dependencies = {}) {
  const issues = [], loginIssues = [], warnings = [];
  const add = (list, code) => list.push({ code, ...guidance(code) });
  const node = { version: process.versions.node, supported: Number(process.versions.node.split('.')[0]) >= 18 };
  if (!node.supported) add(issues, 'NODE_18_REQUIRED');
  let python, runtime;
  try { python = (dependencies.findPython || findPython)(); }
  catch { add(issues, 'PYTHON_39_REQUIRED'); }
  if (python) {
    try { runtime = await (dependencies.inspectRuntime || inspectRuntime)(python); }
    catch { add(loginIssues, 'BROWSER_CONFIG_INVALID'); }
  }
  const session = { file_exists: false, format_valid: false, authenticated: 'not_tested' };
  try {
    const stat = await fs.stat(sessionFile);
    session.file_exists = stat.isFile();
    if (session.file_exists && stat.size <= 32768) {
      const value = JSON.parse((await fs.readFile(sessionFile, 'utf8')).replace(/^\uFEFF/, ''));
      session.format_valid = Boolean(sessionFromBridge(value, value.extension_version));
    }
  } catch { /* Missing and malformed caches require login, not disclosure of their contents. */ }
  const state = options['state-dir'] || stateRoot();
  let extension;
  if (options['extension-dir']) {
    const manifest = await (dependencies.readManifest || readManifest)(options['extension-dir']);
    if (manifest) extension = { manifest };
    else add(loginIssues, 'INVALID_EXTENSION_DIRECTORY');
  } else {
    extension = await (dependencies.discoverExtension || discoverExtension)();
    if (!extension) {
      const manifest = await (dependencies.readManifest || readManifest)(path.join(state, 'extension'));
      if (manifest) extension = { manifest };
    }
    if (!extension) add(loginIssues, 'EXTENSION_NOT_FOUND');
  }
  if (extension && !extensionId(extension.manifest)) add(loginIssues, 'EXTENSION_ID_UNAVAILABLE');
  if (extension && extension.manifest.version !== '5.0.7')
    warnings.push({ code: 'EXTENSION_VERSION_NOT_VERIFIED', message: '当前扩展版本与已验证的 5.0.7 不同，需以本轮接口实测为准。' });
  const desktop = (dependencies.desktopAvailability || desktopAvailability)();
  if (!desktop.available) add(loginIssues, 'INTERACTIVE_BROWSER_REQUIRED');
  if (runtime && !runtime.playwright_installed && !runtime.npm_available) add(loginIssues, 'NPM_REQUIRED');
  const httpReady = !issues.length && session.format_valid;
  const loginPreparable = !issues.length && !loginIssues.length;
  const loginReady = loginPreparable && Boolean(runtime?.playwright_installed && runtime?.browser_installed);
  if (httpReady) warnings.push(...loginIssues);
  else issues.push(...loginIssues);
  return { status: issues.length ? 'needs_setup' : 'ready', skill_version: require('../package.json').version,
    node, python_found: Boolean(python),
    session, session_tested: false, ready_for_http: httpReady, ready_for_login: loginReady,
    login_can_prepare: loginPreparable, login_preparation_required: loginPreparable && !loginReady,
    extension: { found: Boolean(extension), version: extension?.manifest.version || null },
    runtime: runtime || null, desktop, issues, warnings,
    collection_method: 'independent_http', browser_only_for_login: true };
}

module.exports = { guidance, check };
