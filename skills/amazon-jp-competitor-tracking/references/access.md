# 访问方式

## 会话和浏览器

商品快照、销量和修改记录由 `competitor_client.py` 使用 Python 标准库 HTTP 直连 `https://e.sellersprite.com`；当前报价由 `amazon_product.py` 在独立匿名 HTTP 会话中读取。先核对配送国家，已确认日本则直接复用；只有国家非日本或无法确认时才调用地址切换接口并刷新核验。不注册 MCP，不需要飞书权限或 Keepa API key。修改记录来源是卖家精灵自己的 Keepa 替代功能，不宣称直连 Keepa 官方 API。

入口复用已授权的专用 HTTP 会话；本技能首次运行时可读取 POD 选图已知管理目录中的 `http-session.json`，白名单复制到自己的私有目录。只读取这个明确缓存，不读取日常浏览器的 Cookie、密码库或账号存储。浏览器扩展发现只读取已安装扩展的程序文件；授权使用独立持久化 profile，通过原扩展消息同步官网扫码登录，生成 `auth-token`、`auth-fp`、`random-token`。会话不进入仓库或报告。

专用状态目录：

- macOS：`~/Library/Application Support/amazon-jp-competitor-tracking/`
- Windows：`%LOCALAPPDATA%\amazon-jp-competitor-tracking\`
- Linux：`$XDG_DATA_HOME/amazon-jp-competitor-tracking/`，未设置时 `~/.local/share/amazon-jp-competitor-tracking/`

可用 `--state-dir` 或 `AMAZON_COMPETITOR_STATE_DIR` 指定另一个专用目录，不得放在项目仓库、输出目录或日常浏览器 profile 内。目录有互斥锁，避免两个任务同时操作登录态。会话文件权限为 0600，目录为 0700（支持这些权限的平台）。

浏览器下载目录沿用公共 `~/.amazon-skill/config.json` 的 `browser.cache_dir`。卖家精灵授权用的 Playwright 版本固定在本技能 `package.json`，缺失时安装到用户缓存。Amazon 报价补采用 Codex 的浏览器连接和现有的日本配送会话。

没有公共配置文件时直接使用操作系统默认浏览器缓存，不需要创建配置、填写后端 token 或安装其他 Amazon 技能。对方须自行安装 Node.js 18+、Python 3.9+ 和本机卖家精灵原扩展，使用自己的账号扫码。扫码不保证账号具备所有数据权限；403 和业务权限错误按当轮真实结果处理。Windows、Linux 有路径分支，当前实机验收平台是 macOS；Linux 的 Chromium 系统依赖需在目标机另行核验。

## 命令

`SKILL`、`RUN` 都换成实际绝对路径。Windows 可按本机环境将 `python3` 换为 `py -3`。

```sh
# 无网络、无浏览器：检查会话格式、扩展、运行依赖，返回问题与处理方法
node "SKILL/scripts/competitor.cjs" check

# 查看命令帮助，不需要登录或 Python
node "SKILL/scripts/competitor.cjs" --help

# 无网络、无浏览器：完整检查输入和日期区间
node "SKILL/scripts/competitor.cjs" validate-input --input "/absolute/asins.csv"

# 准备授权依赖，启动并关闭本地检查浏览器，不登录网站
node "SKILL/scripts/competitor.cjs" prepare --out "RUN"

# 单独扫码授权；保存成功不代表全部数据接口已验证
node "SKILL/scripts/competitor.cjs" login --out "RUN"

# 正式批量采集；会话有效时无浏览器
node "SKILL/scripts/competitor.cjs" collect --out "RUN" B008H0V15I

# 仅查询标题与亮点的近七天修改时间，不访问 Amazon
node "SKILL/scripts/competitor.cjs" changes --out "RUN" B0CDCD694T

# 回看截至某日的七个自然日期；商品字段仍是本次取得的快照
node "SKILL/scripts/competitor.cjs" collect --out "RUN" --as-of 2026-11-23 B008H0V15I
```

还支持 `--authorization-timeout 300`（5–900 秒）、`--interval 1`（1–60 秒）、`--extension-dir`（已安装的原扩展程序目录）、`--session-file`（用户明确提供的专用会话文件）。Python 可用 `AMAZON_COMPETITOR_PYTHON` 指定；已确认可见桌面时可用 `AMAZON_COMPETITOR_INTERACTIVE=1`，无桌面可设为 0。

`check` 的 `ready_for_http` 仅表示本地会话格式有效，`session.authenticated=not_tested` 表示尚未请求服务端。`ready_for_login` 表示本地登录组件已存在；`login_can_prepare=true` 且 `login_preparation_required=true` 表示可自动准备登录依赖。缺少必需项时为 `needs_setup` 并附 `issues[].next_step`；HTTP 会话可用但登录依赖不齐时，提示放在 `warnings` 中，不强制为普通查询下载浏览器。检查不创建 profile、不登录网站。

## 错误与恢复

- `ERR_NEED_AUTHORIZED`、`ERR_NEED_RE_AUTHORIZED`、`ERR_NEED_RENEWAL_AUTHORIZED`、`ERROR_UNAUTHORIZED`、HTTP 401：停止后续请求，每轮最多扫码恢复一次。
- HTTP 403／429、`ERR_GLOBAL_403`、版本升级要求：停止该轮后续请求，保留结果，不当成登录失败反复弹窗或重试。
- `EXTENSION_NOT_FOUND`：在本机 Chrome／Edge 安装[卖家精灵原扩展](https://www.sellersprite.com/en/help/sellersprite-chrome-extension-download)，或指定原扩展目录。不会打包分发供应商扩展。
- `PYTHON_39_REQUIRED`／`NODE_18_REQUIRED`：安装所需运行时，或修正解释器路径。
- `PLAYWRIGHT_INSTALL_FAILED`／`BROWSER_INSTALL_FAILED`：登录依赖下载失败，检查 npm／浏览器下载网络后运行 `prepare`。
- `BROWSER_LAUNCH_FAILED`：本地浏览器未能启动，运行 `prepare` 定位；Linux 还需核验系统依赖。
- `PROFILE_BUSY`：同一专用目录正被其他进程使用，等待该任务结束。
- `AUTHORIZATION_TIMEOUT`／`AUTHORIZATION_CANCELLED`：保留状态和部分采集结果。再次运行时选新报告目录。
- `OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY`：已有报告目录不能直接复用，改用新的 `RUN`。
- `AMAZON_HUMAN_VERIFICATION_REQUIRED`：Amazon 返回验证页，停止该轮 HTTP 请求。走下述浏览器商品页补采。
- `AMAZON_DESTINATION_NOT_CONFIRMED`／`AMAZON_DESTINATION_REJECTED`：地址切换或刷新核验未通过；不能只凭日本站域名标记配送为日本。具体公开状态保存在 `ASIN-amazon.json`。

任一来源的 HTTP 200 并不表示数据成功，仍检查业务 `code=OK`、返回 ASIN 与字段类型。单个来源网络错误保留其余来源；限流及鉴权错误停止后续接口。报告内不包含请求认证头、账号信息、完整原始响应或浏览器 profile。

## 验证范围

供应商端点和枚举来自原扩展 5.0.7；授权、供应商三个 API 已用 B008H0V15I 实测成功。原扩展在展示 `month_units` 时追加加号，实测接口值 100 已修正为 `100+`，原值另存。商品快照与销量接口的大类排名可能不同，按商品快照优先，证据保留各自原值。

日本配送切换、刷新核验和当前报价解析已有离线流程测试。B008H0V15I 的匿名请求返回验证页。随后从已配送至东京的 Chrome 商品页取得售价、市场价、折扣、购物车与库存状态。独立视频变更仍未接入。

可复制完整技能目录使用，也可安装仓库构建的 ZIP。发布包只包含白名单中的代码和说明，排除登录会话、profile、扩展副本、报告及 node_modules。接收者在自己的环境运行 `check`，按需准备和扫码。完整采集的 `browser_fallback_asins` 明确列出需要 Codex 浏览器工具补采的 ASIN，独立 CLI 不保证在 Amazon 验证页下自动补齐报价。

## 浏览器商品页补采

1. 在 `products.json` 中筛出价格、参考价、折扣、购物车或库存仍缺失的 ASIN。用 Codex 浏览器工具查找同 ASIN 的现有标签页。需要打开时，使用 `https://www.amazon.co.jp/dp/{ASIN}`。
2. 核对商品和日本配送。不确定时查看页面现有配送标签。浏览器商品页遇到验证时由用户完成验证。通过只读页面求值执行 [`amazon_offer_page.js`](../scripts/amazon_offer_page.js) 中的函数。返回对象仅含 URL、商品标题、国家及邮编、报价原文、购物车可用状态和库存文本。将有效对象组成 JSON 数组。页面不匹配或没有取得有效对象时保留原报告并说明缺项。
3. 使用采集脚本合并到新目录。原报告和旧请求证据可以继续查看。`--observations -` 从标准输入读取 JSON。

```sh
python3 "SKILL/scripts/competitor_client.py" merge-browser-offers \
  --from "RUN" --out "NEW_RUN" --observations "/absolute/amazon-offers.json"
```

脚本校验标准商品 URL、ASIN 和日本配送。已见报价和购物车状态的记录保存在 `evidence/ASIN-amazon-browser.json`，JSON 每个相关字段的来源为 `amazon_jp_browser_offer`。这一步可直接用于恢复已有报告，也适用于一批 ASIN。
