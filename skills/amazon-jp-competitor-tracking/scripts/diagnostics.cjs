// Read-only local checks and actionable public errors; never return session headers.
const fs = require('node:fs/promises');
const path = require('node:path');
const { findPython, inspectRuntime, inspectFeishu, desktopAvailability, ensureEnvironment, prepareFeishu, smokeExtension } = require('./environment.cjs');
const { discoverExtension, readManifest, extensionId, stateRoot, prepareExtension } = require('./sellersprite_session.cjs');
const { sessionFromBridge } = require('./sellersprite_auth.cjs');

const GUIDANCE = {
  NODE_18_REQUIRED: ['需要 Node.js 18 或更新版本。', '安装 Node.js 后重新运行 check。'],
  PYTHON_39_REQUIRED: ['未找到 Python 3.9 或更新版本。', '安装 Python，或用 AMAZON_COMPETITOR_PYTHON 指定解释器。'],
  EXTENSION_NOT_FOUND: ['未找到卖家精灵原扩展。', '在 Chrome／Edge 安装原扩展，或用 --extension-dir 指定扩展程序目录。'],
  INVALID_EXTENSION_DIRECTORY: ['指定目录不是可用的卖家精灵原扩展。', '将 --extension-dir 指向包含 manifest.json 和 popup 页的程序目录。'],
  EXTENSION_ID_UNAVAILABLE: ['扩展未提供授权桥接所需的标识。', '安装 Chrome 商店的原扩展后重试。'],
  EXTENSION_LOAD_FAILED: ['卖家精灵扩展未能在本机 Chromium 中加载。', '核对原扩展版本和浏览器安装后重跑 prepare。'],
  INTERACTIVE_BROWSER_REQUIRED: ['需要可见桌面完成扫码登录。', '在本地桌面运行；确认桌面可用后可设置 AMAZON_COMPETITOR_INTERACTIVE=1。'],
  NPM_REQUIRED: ['未找到用于安装登录依赖的 npm。', '安装包含 npm 的 Node.js，然后运行 prepare。'],
  PLAYWRIGHT_MISSING: ['登录所需的 Playwright 尚未准备。', '运行 prepare；有效 HTTP 会话查询不需要它。'],
  BROWSER_PREPARATION_REQUIRED: ['Playwright 或 Chromium 尚未准备。', '运行 prepare 安装并验证浏览器依赖。'],
  PLAYWRIGHT_INSTALL_FAILED: ['登录依赖下载或安装失败。', '检查 npm 网络或代理设置，再运行 prepare。'],
  BROWSER_INSTALL_FAILED: ['登录浏览器下载失败。', '检查下载网络或代理设置，再运行 prepare。'],
  BROWSER_LAUNCH_FAILED: ['专用登录浏览器无法启动。', '运行 prepare 检查浏览器；Linux 还需安装 Chromium 系统依赖。'],
  BROWSER_CONFIG_INVALID: ['浏览器缓存配置无法读取。', '检查 ~/.amazon-skill/config.json 中 browser.cache_dir，要求是本机绝对路径。'],
  FEISHU_CLI_REQUIRED: ['未找到飞书官方 CLI。', '运行 prepare --feishu --out 新目录安装固定版本。'],
  FEISHU_CLI_NOT_RUNNABLE: ['飞书 CLI 无法执行或未返回版本。', '检查指定路径、可执行权限及系统架构。'],
  FEISHU_CLI_INSTALL_FAILED: ['飞书 CLI 安装失败。', '检查 npm 和官方 CLI 下载网络后重跑 prepare --feishu。'],
  FEISHU_CLI_CHECK_FAILED: ['未能完成飞书 CLI 本地检查。', '检查 Python 和 lark-cli 是否可执行，再运行 check --feishu。'],
  FEISHU_CLI_AUTH_STATUS_FAILED: ['飞书 CLI 未返回可用的授权状态。', '运行 lark-cli auth status 检查本机飞书 CLI 配置。'],
  FEISHU_CLI_APP_REQUIRED: ['飞书 CLI 尚未配置应用。', '通过 lark-cli config init 完成应用配置。'],
  FEISHU_CLI_USER_AUTH_REQUIRED: ['飞书 CLI 尚无可用用户授权。', '按飞书 CLI 工作流完成用户授权。'],
  FEISHU_CLI_SCOPE_REQUIRED: ['飞书 CLI 用户授权缺少表格操作所需权限。', '按 missing_scopes 核对，再以用户身份补充授权。'],
  PROFILE_BUSY: ['另一个任务正在使用同一专用会话。', '等待该任务结束，或用独立 --state-dir 运行另一个账号。'],
  AUTHORIZATION_TIMEOUT: ['卖家精灵插件登录等待超时。', '用新报告目录重试，并在日本站商品页的插件面板完成登录。'],
  AUTHORIZATION_CANCELLED: ['卖家精灵专用窗口已关闭。', '用新报告目录重试，授权完成前保持窗口打开。'],
  AUTHORIZATION_REJECTED: ['卖家精灵未接受本次授权。', '核对自己的账号登录和权限，用新报告目录重试。'],
  PLUGIN_LOGIN_REQUIRED: ['卖家精灵返回星号登录遮罩，当前数据尚未解锁。', '完成本轮浏览器登录后重查；遮罩不写入表格，也不代表没有图片变化。'],
  OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY: ['输出目录已有报告或采集证据。', '每次查询选择新的 --out 目录。'],
  ERR_GLOBAL_403: ['卖家精灵拒绝了这个接口请求。', '检查当前账号的数据权限或扩展版本；本轮不会重复弹登录窗口。'],
  ERR_ROBOT_CHECK: ['卖家精灵返回验证拒绝，已暂停共享采集请求并保留已有数据。', '此错误本身不能确认验证码原因；处理同一专用会话的登录或验证后运行 verify-session，通过 API 核验才恢复。'],
  ERR_ACCOUNT_NOT_EXIST: ['卖家精灵令牌登录接口返回账号不可用。', '检查插件所用账号与可用的官网账号是否一致。确认插件登录并通过 API 核验后再继续。'],
  AMAZON_CONTINUE_SHOPPING_REQUIRED: ['Amazon 返回继续购物确认页，尚未取得有效商品报价。', '使用 amazon-offer ASIN --amazon-browser headed --out 新目录，在同一专用会话中由用户完成交互后恢复。'],
  AMAZON_HUMAN_VERIFICATION_REQUIRED: ['Amazon 要求验证，已暂停后续 Amazon 请求。', '使用 amazon-offer ASIN --amazon-browser headed --out 新目录，由用户完成验证并等待采集核验成功。'],
  PROVIDER_VERIFICATION_PENDING: ['其他任务已因验证拒绝暂停共享采集。', '处理同一专用会话后运行 verify-session；不要启动多个登录或反复请求。'],
  PROVIDER_AUTHORIZATION_PENDING: ['共享采集正在等待账号授权恢复。', '由一个任务完成 login 并通过 API 核验，其他任务随后续跑原 job_id。'],
  PROVIDER_BUSY: ['另一个任务正在使用共享采集资源。', '等待该任务释放后续跑原 job_id，不重新规划同一批记录。'],
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
  let python, runtime, feishu;
  try { python = (dependencies.findPython || findPython)(); }
  catch { add(issues, 'PYTHON_39_REQUIRED'); }
  if (python) {
    const [browserCheck, feishuCheck] = await Promise.allSettled([
      (dependencies.inspectRuntime || inspectRuntime)(python),
      (dependencies.inspectFeishu || inspectFeishu)(python),
    ]);
    if (browserCheck.status === 'fulfilled') runtime = browserCheck.value;
    else add(loginIssues, 'BROWSER_CONFIG_INVALID');
    feishu = feishuCheck.status === 'fulfilled' ? feishuCheck.value : { ready: false, category: 'FEISHU_CLI_CHECK_FAILED' };
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
  const read = dependencies.readManifest || readManifest;
  const cached = await read(path.join(state, 'extension'));
  let extension, extensionSource;
  if (options['extension-dir']) {
    const manifest = await read(options['extension-dir']);
    if (manifest) extension = { manifest };
    else add(loginIssues, 'INVALID_EXTENSION_DIRECTORY');
    extensionSource = 'specified_directory';
  } else {
    extension = await (dependencies.discoverExtension || discoverExtension)();
    if (extension) extensionSource = 'installed_browser';
    else if (cached) {
      extension = { manifest: cached };
      extensionSource = 'skill_cache';
    }
    if (!extension) add(loginIssues, 'EXTENSION_NOT_FOUND');
  }
  if (extension && !extensionId(extension.manifest)) add(loginIssues, 'EXTENSION_ID_UNAVAILABLE');
  if (extension && extension.manifest.version !== '5.0.7')
    warnings.push({ code: 'EXTENSION_VERSION_NOT_VERIFIED', message: '当前扩展版本与已验证的 5.0.7 不同，需以本轮接口实测为准。' });
  const desktop = (dependencies.desktopAvailability || desktopAvailability)();
  if (!desktop.available) add(loginIssues, 'INTERACTIVE_BROWSER_REQUIRED');
  if (runtime && (!runtime.playwright_installed || !runtime.browser_installed)) add(warnings, 'BROWSER_PREPARATION_REQUIRED');
  if (runtime && !runtime.playwright_installed && !runtime.npm_available) add(loginIssues, 'NPM_REQUIRED');
  const httpReady = !issues.length && session.format_valid;
  const loginPreparable = !issues.length && !loginIssues.length;
  const loginReady = loginPreparable && Boolean(runtime?.playwright_installed && runtime?.browser_installed);
  if (httpReady) warnings.push(...loginIssues);
  else issues.push(...loginIssues);
  if (feishu?.category) add(options.feishu ? issues : warnings, feishu.category);
  return { status: issues.length ? 'needs_setup' : 'ready', skill_version: require('../package.json').version,
    node, python_found: Boolean(python), python_version: python?.version || null,
    session, session_tested: false, ready_for_http: httpReady, ready_for_login: loginReady,
    login_can_prepare: loginPreparable, login_preparation_required: loginPreparable && !loginReady,
    extension: { found: Boolean(extension), version: extension?.manifest.version || null,
      source: extension ? extensionSource : null, cached_version: cached?.version || null, load_tested: false },
    runtime: runtime || null, desktop, feishu: feishu ? { required: Boolean(options.feishu), ...feishu } : null, issues, warnings,
    collection_method: 'http_with_optional_amazon_browser', browser_only_for_login: false };
}

async function prepareSetup(options, home, python, output, dependencies = {}) {
  const issues = [], warnings = [];
  const [browserResult, extensionResult, feishuResult] = await Promise.allSettled([
    (dependencies.ensureEnvironment || ensureEnvironment)({ prepare: true }),
    (dependencies.prepareExtension || prepareExtension)(home, options['extension-dir']),
    (options.feishu ? dependencies.prepareFeishu || prepareFeishu : dependencies.inspectFeishu || inspectFeishu)(python),
  ]);
  const browser = browserResult.value, extension = extensionResult.value;
  const feishu = feishuResult.value || { ready: false, category: 'FEISHU_CLI_CHECK_FAILED' };
  const add = (list, code) => list.push({ code, ...guidance(code) });
  if (!browser) add(issues, browserResult.reason?.code || 'BROWSER_LAUNCH_FAILED');
  if (!extension) add(issues, extensionResult.reason?.code || 'EXTENSION_NOT_FOUND');
  else if (!extension.id) add(issues, 'EXTENSION_ID_UNAVAILABLE');
  const extensionStatus = { prepared: Boolean(extension?.id), version: extension?.manifest.version || null,
    load_tested: false, loaded: null };
  if (browser && extension?.id) {
    extensionStatus.load_tested = true;
    try {
      await (dependencies.smokeExtension || smokeExtension)(browser.playwright, extension);
      extensionStatus.loaded = true;
    } catch {
      extensionStatus.loaded = false;
      add(issues, 'EXTENSION_LOAD_FAILED');
    }
  }
  if (feishu.category) add(options.feishu ? issues : warnings, feishu.category);
  const ready = issues.length === 0;
  const report = { ...browser?.report, status: ready ? 'ready' : 'needs_setup', ready,
    extension: extensionStatus, feishu: { required: Boolean(options.feishu), ...feishu },
    desktop: desktopAvailability(), issues, warnings, credentials_included: false };
  const directory = path.join(output, 'environment');
  await fs.mkdir(directory, { recursive: true });
  await fs.writeFile(path.join(directory, 'environment.json'), JSON.stringify(report, null, 2));
  return report;
}

module.exports = { guidance, check, prepareSetup };
