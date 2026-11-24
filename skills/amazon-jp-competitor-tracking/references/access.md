# 访问方式

## 会话和浏览器

商品快照、销量和修改记录由 `competitor_client.py` 使用 Python 标准库 HTTP 直连 `https://e.sellersprite.com`；Amazon 当前报价优先使用独立 HTTP 会话，失败或无价格时由普通无头 Chromium 自动补采。HTTP Cookie 保存在专用目录的 `amazon-http-cookies.txt`，浏览器状态保存在 `amazon-profile`；两者均不读取日常浏览器登录库。先核对配送国家，已确认日本则直接复用；只有国家非日本或无法确认时才调用地址切换接口并刷新核验。不注册 MCP，不需要 Keepa API key。本地采集无需飞书权限；飞书读写另按 [飞书 CLI 工作流](feishu.md) 授权。修改记录来源是卖家精灵自己的 Keepa 替代功能，不宣称直连 Keepa 官方 API。

入口复用已授权的专用 HTTP 会话；本技能首次运行时可读取 POD 选图已知管理目录中的 `http-session.json`，白名单复制到自己的私有目录。只读取这个明确缓存，不读取日常浏览器的 Cookie、密码库或账号存储。浏览器扩展发现只读取已安装扩展的程序文件；授权使用独立持久化 profile，通过原扩展消息同步官网扫码登录，生成 `auth-token`、`auth-fp`、`random-token`。会话不进入仓库或报告。

专用状态目录：

- macOS：`~/Library/Application Support/amazon-jp-competitor-tracking/`
- Windows：`%LOCALAPPDATA%\amazon-jp-competitor-tracking\`
- Linux：`$XDG_DATA_HOME/amazon-jp-competitor-tracking/`，未设置时 `~/.local/share/amazon-jp-competitor-tracking/`

可用 `--state-dir` 或 `AMAZON_COMPETITOR_STATE_DIR` 指定另一个专用目录，不得放在项目仓库、输出目录或日常浏览器 profile 内。目录有互斥锁，避免两个任务同时操作登录态。会话文件权限为 0600，目录为 0700（支持这些权限的平台）。

浏览器下载目录沿用公共 `~/.amazon-skill/config.json` 的 `browser.cache_dir`。Playwright 版本固定在本技能 `package.json`，缺失时安装到用户缓存。Amazon 使用 `channel: chromium` 的原生新无头模式，保留浏览器默认自动化标识，没有 stealth、代理轮换或验证码处理组件。其独立持久 profile 与卖家精灵授权 profile 隔离。[Playwright 官方说明](https://playwright.dev/docs/browsers)介绍了该无头模式；持久 profile 及互斥约束见 [launchPersistentContext](https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context)。

没有公共配置文件时直接使用操作系统默认浏览器缓存，不需要创建配置、填写后端 token 或安装其他 Amazon 技能。对方须自行安装 Node.js 18+、Python 3.9+ 和本机卖家精灵原扩展，使用自己的账号扫码。扫码不保证账号具备所有数据权限；403 和业务权限错误按当轮真实结果处理。Windows、Linux 有路径分支，当前实机验收平台是 macOS；Linux 的 Chromium 系统依赖需在目标机另行核验。

## 命令

`SKILL`、`RUN` 都换成实际绝对路径。Windows 可按本机环境将 `python3` 换为 `py -3`。

```sh
# 本地检查会话格式、扩展、运行依赖和飞书 CLI 状态
node "SKILL/scripts/competitor.cjs" check
node "SKILL/scripts/competitor.cjs" check --feishu

# 查看命令帮助，不需要登录或 Python
node "SKILL/scripts/competitor.cjs" --help

# 无网络、无浏览器：完整检查输入和日期区间
node "SKILL/scripts/competitor.cjs" validate-input --input "/absolute/asins.csv"

# 准备 Chromium 和卖家精灵原扩展，在临时无头 Chromium 中离线试载
node "SKILL/scripts/competitor.cjs" prepare --out "RUN"
# 飞书场景同时准备官方 CLI 和检查本地授权权限
node "SKILL/scripts/competitor.cjs" prepare --feishu --out "RUN"

# 从日本站商品页进入原插件登录并核验 API
node "SKILL/scripts/competitor.cjs" login --out "RUN"

# 验证拒绝处理后，以同一专用会话进行 API 核验，成功才解除共享暂停
node "SKILL/scripts/competitor.cjs" verify-session --out "NEW_RUN"

# 正式批量采集；Amazon HTTP 无价格时自动无头补采
node "SKILL/scripts/competitor.cjs" collect --out "RUN" B008H0V15I

# 仅查询近七天“修改图片/视频”合并图标，必要时在日本站完成插件授权
node "SKILL/scripts/competitor.cjs" changes --out "RUN" B0CDCD694T

# 回看截至某日的七个自然日期；商品字段仍是本次取得的快照
node "SKILL/scripts/competitor.cjs" collect --out "RUN" --as-of 2026-11-23 B008H0V15I
```

还支持 `--authorization-timeout 300`（5–900 秒）、`--interval 1`（1–60 秒）、`--extension-dir`（已安装的原扩展程序目录）、`--session-file`（用户明确提供的专用会话文件）。Python 可用 `AMAZON_COMPETITOR_PYTHON` 指定；已确认可见桌面时可用 `AMAZON_COMPETITOR_INTERACTIVE=1`，无桌面可设为 0。

`check` 不联网、不开浏览器。`ready_for_http` 表示本地会话格式有效，卖家精灵的服务端会话用 `verify-session` 单独验证。`ready_for_login` 表示本地登录组件已存在。`extension.cached_version` 是 Skill 专用副本的版本。缺少必需项时返回 `needs_setup` 和 `issues[].next_step`；可选项的提示在 `warnings`，仅对飞书查询传 `--feishu` 将 CLI 和相应用户权限视为必需。

`prepare` 按需安装固定版本的 Playwright 和 Chromium，从已安装的浏览器扩展中准备专用副本，再使用临时无头浏览器校验扩展能否加载。检查结果写入 `RUN/environment/environment.json`。如果卖家精灵原扩展尚未安装，先通过浏览器安装[卖家精灵官方扩展](https://www.sellersprite.com/en/help/sellersprite-chrome-extension-download)，然后重跑。`prepare --feishu` 缺少 CLI 时安装固定版本到用户缓存；应用配置、授权步骤和线上核验见 [飞书 CLI 工作流](feishu.md)。

## 错误与恢复

- `ERR_NEED_AUTHORIZED`、`ERR_NEED_RE_AUTHORIZED`、`ERR_NEED_RENEWAL_AUTHORIZED`、`ERROR_UNAUTHORIZED`、HTTP 401：有专用令牌时先执行一次 API 续登并重试，仍需登录才停止后续取数并进入一次浏览器授权。
- `PLUGIN_LOGIN_REQUIRED`：API 数据包含 `****` 等纯星号登录遮罩。即使 `code=OK` 也要求恢复登录；API 续登后仍被遮罩则进入一次浏览器授权，成功后重查被遮罩的来源。
- `ERR_ROBOT_CHECK`：返回验证拒绝并暂停同机共享采集；仅凭该错误不能证明触发 CAPTCHA，更不能直接归因于并发。保留已有数据，处理同一专用会话后用 `verify-session` 核验，成功后继续 API；不自动改为 Browser Use 采集。
- `PROVIDER_VERIFICATION_PENDING`／`PROVIDER_AUTHORIZATION_PENDING`：另一个任务已暂停共享请求，由一个执行者恢复，其他任务不重复弹窗。
- `PROVIDER_BUSY`：共享资源被占用，等待后续跑原 job_id，不重复规划。
- HTTP 403／429、`ERR_GLOBAL_403`、版本升级要求：停止该轮后续请求，保留结果，不当成登录失败反复弹窗或重试。
- `EXTENSION_NOT_FOUND`：在本机 Chrome／Edge 安装[卖家精灵原扩展](https://www.sellersprite.com/en/help/sellersprite-chrome-extension-download)，或指定原扩展目录。不会打包分发供应商扩展。
- `PYTHON_39_REQUIRED`／`NODE_18_REQUIRED`：安装所需运行时，或修正解释器路径。
- `PLAYWRIGHT_INSTALL_FAILED`／`BROWSER_INSTALL_FAILED`：登录依赖下载失败，检查 npm／浏览器下载网络后运行 `prepare`。
- `BROWSER_LAUNCH_FAILED`：本地浏览器未能启动，运行 `prepare` 定位；Linux 还需核验系统依赖。
- `PROFILE_BUSY`：同一专用目录正被其他进程使用，等待该任务结束。
- `AUTHORIZATION_TIMEOUT`／`AUTHORIZATION_CANCELLED`：保留状态和部分采集结果。再次运行时选新报告目录。
- `OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY`：已有报告目录不能直接复用，改用新的 `RUN`。
- `AMAZON_CONTINUE_SHOPPING_REQUIRED`：返回“继续购物”确认页，区别于验证码输入页，不自动点击确认按钮。
- `AMAZON_HUMAN_VERIFICATION_REQUIRED`：返回验证码输入页或未识别的验证表单。无头仍受阻时，按下述可见浏览器流程由用户处理；共享暂停期间不再为其他 ASIN 连续发请求。
- `AMAZON_DESTINATION_NOT_CONFIRMED`／`AMAZON_DESTINATION_REJECTED`：地址切换或刷新核验未通过；不能只凭日本站域名标记配送为日本。具体公开状态保存在 `ASIN-amazon.json`。

任一来源的 HTTP 200 并不表示数据成功，仍检查业务 `code=OK`、返回 ASIN 与字段类型。单个来源网络错误保留其余来源；限流及鉴权错误停止后续接口。报告内不包含请求认证头、账号信息、完整原始响应或浏览器 profile。

## API 续登和空数据

扩展 5.0.7 的“登录 xxx”按钮使用已登录官网账号的令牌，发送 `SIGN_IN_WITH_TOKEN`，后台实际请求 `GET https://www.sellersprite.com/v2/extension/tk/signin`。本技能使用已授权专用会话中的令牌执行同一接口；只接受成功响应中的有效新 token，原子写回私有会话文件，不保存返回的账号资料，也不读取日常浏览器账号存储。

正常取数成功即可复用会话，不逐次登录。明确鉴权错误才尝试一次令牌登录。普通空商品、待缓存商品、商品核心字段或销量缺项（包括负数 units），每个 HTTP 工作进程至多用签名后的 `client/latest` 核验一次会话；仅令牌实际更新才重查当前来源。核验有效但仍缺失时保留未知，不再次续登。合法空修改历史和 Amazon 报价缺项不触发卖家精灵续登。

纯星号字段是明确的未登录遮罩，不属于上述普通空值。`code=OK` 加被遮罩的价格／销量等目标商品字段也会触发 `PLUGIN_LOGIN_REQUIRED`；重试后仍有遮罩就进入浏览器登录，不把它解释成来源没有数据。接口拒绝登录时仍保留同一响应中未遮罩的有效字段，被遮罩字段转为 null；来源标记 `recovery_pending`，恢复后必须重查，不能复用为成功缓存。飞书写入层还会拒绝含原始星号遮罩的商品报告。

页面显示“插件需要登录才能使用／立即登录”反映该浏览器扩展的状态。采集器使用独立 API 会话，以 API 响应判断登录是否有效，不需要等 Keepa 面板加载或点击页面按钮。API 会话失效时执行同一账号令牌登录流程，必要时才扫码。页面空白和登录遮罩均不能作为近七天没有图库变化的证据。

续登成功后若重试仍为空，保留未知。`status.json.api_authorization.events` 记录续登触发原因和结果。令牌续登返回 `ERR_ACCOUNT_NOT_EXIST` 时，任务进入插件交互登录，原始拒绝码仍保存在续登事件中。可见窗口由原扩展处理登录，脚本只等待并验证插件登录结果。

显式运行 `login --out RUN --login-asin B09BQ4KNTG` 可选择要打开的日本站商品。`collect` 和 `changes` 默认使用本轮第一个 ASIN，单独运行 `login` 时默认打开日本站首页。在窗口工具栏的扩展程序菜单里打开卖家精灵，点击「立即登录」，再在 Amazon 页面内的原插件面板完成登录。

2026-11-24 日本站实测中，官网已有会话，但插件的 `tk/signin` 返回 `ERR_ACCOUNT_NOT_EXIST`。用户在商品页内直接登录后，原插件的 `/v2/extension/signin` 返回 `OK`，随后浏览器和独立脚本调用 `/v2/extension/client/latest` 均返回 `OK`。

2026-11-24 复查扩展 5.0.7：公共请求中间件为商品、修改历史、会话检查及续登等请求统一补 `tk`，原脚本只在销量请求显式携带。这一差异现已修复，签名输入优先 `asins/asin/q`，否则使用路径；六组输入与原扩展校验函数一致。补齐后的一次串行、只读 `client/latest` 实测仍返回 `ERR_ROBOT_CHECK`，因此不能声称签名修复已恢复服务，也不能认定并发是唯一原因。未继续高频探测。

同机共享 `ProviderGate` 将本方案发出的卖家精灵 HTTP 请求串行化，跨进程请求起始间隔至少一秒，令牌续登也受同一锁保护；一秒是保守默认值，不代表供应商保证的安全速率。飞书执行器默认每批最多 3 个 ASIN，同机多个任务合计也最多 3 个；整段采集／登录恢复另外串行化，已取得数据的 ASIN 立即通过 CLI 写回。普通 Chrome 扩展、自行运行的旧脚本等非配合请求不受该锁控制，打开多个商品页可能产生额外后台请求。不要通过改 state-dir 为同一账号制造并发登录。

[卖家精灵官方扩展自检说明](https://cn.sellersprite.com/v3/knowledge/feature/extension-self-check-guide) 提到不同浏览器环境登录可能互相挤下线，网络环境变化也可能触发验证；这是排查方向，尚未证明是本轮拒绝的原因。普通 Chrome 已登录不等于独立管理的 API 会话有效。`verify-session` 只检查同一专用会话，不自动轮换令牌；失败维持暂停。该检查只用于恢复或缺项诊断，不在每个正常请求前额外调用。公开 `request_trace` 仅有接口路径、参数名、请求起止时间及状态，不含认证头和参数值。

## 验证范围

供应商端点和枚举来自原扩展 5.0.7；授权、供应商三个 API 已用 B008H0V15I 实测成功。原扩展在展示 `month_units` 时追加加号，实测接口值 100 已修正为 `100+`，原值另存。商品快照与销量接口的大类排名可能不同，按商品快照优先，证据保留各自原值。

日本配送切换、刷新核验和当前报价解析已有离线流程测试。B008H0V15I 的匿名请求返回验证页。随后从已配送至东京的 Chrome 商品页取得售价、市场价、折扣、购物车与库存状态。图库变化采用插件“修改图片/视频”的合并图标口径。

可复制完整技能目录使用，也可安装仓库构建的 ZIP。发布包只包含白名单中的代码和说明，排除登录会话、profile、扩展副本、报告及 node_modules。接收者在自己的环境运行 `check`，按需准备和扫码。`browser_fallback_asins` 列出自动补采后仍有报价缺项的商品；折扣或参考价可能本就不展示，不能据此反复请求。`amazon_browser_verification_asins` 列出需要可见浏览器恢复的商品。

2026-11-24 使用 B0D9W5646D 实测：匿名 HTTP 返回继续购物确认页，随后原生无头浏览器成功取得售价 5,387 日元、购物车可用与在库；通过地址接口将配送设置为日本并刷新核验。没有隐藏无头标识、处理验证码或借用日常 Chrome Cookie。单个样本成功不保证所有商品和网络环境均可访问。

## CLI 浏览器恢复

```sh
# 只测 Amazon，不依赖卖家精灵登录；默认无头，输出公开报价快照
node "SKILL/scripts/competitor.cjs" amazon-offer B0D9W5646D --out "NEW_RUN"

# 确认或验证页：用同一个 --state-dir，在可见窗口等待用户操作
node "SKILL/scripts/competitor.cjs" amazon-offer B0D9W5646D \
  --amazon-browser headed --authorization-timeout 300 --out "RECOVERY_RUN"
```

`amazon-offer.json` 保存同一 ASIN、日本配送和当前报价。只有成功采集才将 `amazon-browser-status.json` 改为 ready；验证超时、错误商品或配送不明不会解除暂停。恢复后用新目录运行 `collect`；飞书继续尚未完成的行，已经以缺项交付的行需要安排更新采集，不把旧快照当成新报价。不能把可见窗口“看起来通过”当成采集成功。独立 HTTP 会话与浏览器会话并不自动互相继承 Cookie，后续仍按 HTTP 优先、必要时浏览器补采执行。

## 浏览器商品页补采

以下是 CLI 自动后备之外的可选人工补采路径，不用于飞书读写。

1. 在 `products.json` 中筛出需要补采的 ASIN，排除页面本就没有的折扣和参考价。用 Codex 浏览器工具查找同 ASIN 的现有标签页。需要打开时，使用 `https://www.amazon.co.jp/dp/{ASIN}`。
2. 核对商品和日本配送。不确定时查看页面现有配送标签。浏览器商品页遇到验证时由用户完成验证。通过只读页面求值执行 [`amazon_offer_page.js`](../scripts/amazon_offer_page.js) 中的函数。返回对象仅含 URL、商品标题、国家及邮编、报价原文、购物车可用状态和库存文本。将有效对象组成 JSON 数组。页面不匹配或没有取得有效对象时保留原报告并说明缺项。
3. 使用采集脚本合并到新目录。原报告和旧请求证据可以继续查看。`--observations -` 从标准输入读取 JSON。

```sh
python3 "SKILL/scripts/competitor_client.py" merge-browser-offers \
  --from "RUN" --out "NEW_RUN" --observations "/absolute/amazon-offers.json"
```

脚本校验标准商品 URL、ASIN 和日本配送。已见报价和购物车状态的记录保存在 `evidence/ASIN-amazon-browser.json`，JSON 每个相关字段的来源为 `amazon_jp_browser_offer`。这一步可直接用于恢复已有报告，也适用于一批 ASIN。
