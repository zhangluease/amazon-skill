# 花瓣访问与接口

验证日期：2026-11-12。以下是网站当前使用的内部接口，不是官方开放 API 承诺。使用前核对当前页面，结构变化时回到正常浏览器流程。

## 本次实测

| 能力 | 已验证结果 | 边界 |
| --- | --- | --- |
| 已登录 Chrome 搜索“碎花 水彩” | 搜索结果正常，关键词与图片链接可从 AX/DOM 读取 | 图片的视觉风格仍需看图 |
| 图片右键找相似 | 打开 `/pins/6498350553/similar`，正常显示相似采集等分类 | 详情页商用相似列表与独立相似采集页并非同一批候选 |
| 详情推荐画板 | “小清新”“四方连续图”等；preview JSON 与当前页面画板对应 | 所在画板可由页面单独加在前面 |
| 已登录浏览器直接导航到 JSON URL | 搜索返回 3 张；相似返回 1 张；推荐画板返回 2 个，每个 3 张预览 | 证明浏览器会话内成功；未做独立客户端带 Cookie 对照 |
| Python，无 Cookie/Authorization | `/v3/pins/...` 等 4 个 GET 返回 405 | 不能认定接口不存在，或单独认定为缺 Cookie |
| Python，普通页面 GET | discovery 返回含 `__NEXT_DATA__` 的 HTML；search、pin、similar 返回约 1 KB JS 校验页，HTTP 200 | 校验页含 `__tst_status`、`EO_Bot_Ssid`；不运行或伪造它，改用浏览器 |
| 图片导出 | Python 请求图片 CDN 返回 567；本地图集的三张跨站外链全部未加载；Chrome 原生保存两张预览成功 | 不能只把 file.url 放进 HTML 就声称已交付图集 |
| DOM 提取脚本 | 已提取 38 张已呈现卡片，尺寸标为预览尺寸 | 数量随当前页面状态变化，不代表全量搜索 |
| 第二轮独立 Playwright 无头脚本 | 未导入登录态，“狗 动漫”38 张约 3.3 秒；相似图与画板一轮约 8 秒、60 个候选均缓存成功 | 使用本机 Chrome、普通 Chrome UA，先发现页再搜索；本次环境测量，不保证所有环境 |
| 默认无头与有界面对照 | 默认 HeadlessChrome UA 的无头请求 405；复用游客 Cookie 仍 405；普通 Chrome UA 的无头请求成功 | 不把 405 直接认定为缺登录；同为匿名，有界面脚本约 5.9 秒取得 38 张并捕获图片响应 |

## 已观察的路由

API 站点前缀为 `https://huaban.com/v3`，页面为 `https://huaban.com`。

| 用途 | 路径与参数 | 数据位置 |
| --- | --- | --- |
| 搜索采集 | `GET /search/file?text=碎花%20水彩&sort=all&limit=3&page=1&position=search_pin&relations=false&all_match=true` | `pins[]`, `total`, `split_words` |
| 详情 | `GET /pins/{pin_id}`，页面可传 `fields` | `pin`；本次 Network 观察到详情请求 200 |
| 相似采集 | `GET /pins/{pin_id}/similar?limit=20`，后续可带 `max` | `pins[]`, `file`；max 的实际分页本次未实测 |
| 推荐画板预览 | `GET /pins/{pin_id}/relatedboardsweb-preview` | `boards[]`，每个有 `pins[]` |
| 更多推荐画板 | `GET /pins/{pin_id}/relatedboardsweb?limit=2&page=1&pins_limit=3` | `boards[].pins[]`；第二页本次未实测 |
| 搜索页面 | `/search?q=碎花%20水彩` | 搜索框与图片卡片 |
| 图片详情 / 相似页面 | `/pins/{pin_id}` / `/pins/{pin_id}/similar` | 正常页面可点击入口 |
| 画板页面 | `/boards/{board_id}` | 实际图片卡片，按需滚动 |

搜索 Network 中还观察到 `cluster_search=string_4`、`cluster_response=compact_v1`、`fields=...`。它们会随实验变化；最小参数浏览器请求已成功，不固化这些实验值。不要猜 `type=board` 一定对应同一个采集搜索接口。

更多 API 在公开脚本中可见，如 `recommendweb`、`recommendations`、`relatedboardsweb-explore`、`boards/{id}/raw_pins`；这里只记录线索，不将未成功验证的请求当作完成取数。新增能力先观察当前 UI 的网络请求。

## 三种执行方式

### 0. 独立 Playwright 脚本：当前默认

`scripts/huaban_browser.cjs` 使用完整 Playwright，默认不导入用户 Cookie，不读取日常 Chrome 的账号或配置。先访问发现页，再导航到关键词搜索、相似页或已知画板；通过 `response.json()` 和图片响应的 `response.body()` 得到数据、缓存图片。不需要逐张 Browser Use，也不需要额外向 CDN 重放下载请求。

运行环境：Node.js 18+、Python 3.9+、Chrome 或管理版 Chromium、Playwright 1.61.0（本次实测版本）。采集脚本自动校验环境并按需将运行库安装到用户缓存，无需用户在提示词中指定。独立排障命令详见[环境自检](environment.md)。采集命令：

```sh
node scripts/huaban_browser.cjs --query '狗 动漫' --limit 40 --out /absolute/output/search
node scripts/huaban_browser.cjs --similar 6119259200 --board 56207432 --out /absolute/output/expand
node scripts/huaban_browser.cjs --board 56207432 --source-file-id 906964761 --out /absolute/output/recommended
```

输出为 `response.json`（可直接 normalize）、`browser-candidates.json`（来源关系）、`browser-assets/`、页面截图与 `timing.json`。失败保存诊断截图和状态，不将错误页作为成功候选。视觉选择仍须实际查看截图或图集。

从详情推荐画板进入时，若实际链接含 `source_file_id`，使用 `--source-file-id` 保留。它会影响画板的相似排序；省略时可能显示按时间排序的另一批图片。脚本按实际渲染的卡片顺序截取候选，并防止上一个页面延迟返回的响应串入下一个页面的来源记录。

默认无头 Chrome 的 User-Agent 含 HeadlessChrome，本次返回 405。脚本只将该名称保持为普通 Chrome，保留本机实际 Chrome 版本；未使用 stealth 插件或更改 webdriver。匿名无头在此设置下已实测成功。不能把这一兼容设置当作长期访问保证或对所有 405 的通用解决法。

无头模式与 AX/ARIA 是两个维度：前者控制是否显示窗口，后者是页面语义结构。脚本可同时使用无头模式和语义定位；无障碍树不会替代网站 JavaScript、登录态、网络请求或图片视觉判断。有界面脚本 `--headed` 同样批量执行，提速主要来自减少逐步工具调用。

遇到明确真人验证时，默认自动打开可见浏览器等待用户手动完成，再沿用当前可见会话采集。本轮会话只在内存中迁移，不保证跨设备或跨出口 IP 的验证态可用。云端/xvfb 无用户接管入口时返回明确状态，不反复换模式探测。状态日志与使用方式见[人机验证接管](environment.md#人机验证接管)。

首次使用不要求已有账号：先尝试公开内容。本次匿名搜索、相似页与画板可用。遇到确切登录要求时再由用户在可见浏览器完成登录；已有用户提供的 Playwright 会话文件可用 `--state /absolute/private/huaban-state.json`。会话复用是正式支持的机制，但本次未对已登录账号的状态文件做对照验证。不要把整个日常浏览器配置复制到脚本，也不要在遇到验证码时自动处理。

### A. 已登录 Browser Use 正常页面：备用路径

搜索框可通过 accessible name“搜索花瓣内容”定位。搜索结果的采集是 `a[href*="/pins/"]` 下的 `img`；右键菜单“找相似”可通过 AX/可见文字操作。详情“推荐画板”是文字与链接组合，未必是标准 button。

只读 DOM 提取函数为 `scripts/extract_cards.js`，适合让当前工具在允许的 DOM evaluate 中执行。它只读取已呈现的图片与其采集链接，尺寸标为缩略图尺寸，不访问 Cookie、localStorage、React store 或 `__NEXT_DATA__`。可用截图/页面图片完成视觉检查。

在浏览器直接导航到已验证的 JSON URL也已成功。JSON 格式化扩展可能把键名改成无引号的显示文本；若看到“view source”，先点击再读取 `<pre>` 中原文。不要把格式化后的 innerText 当作合法 JSON。若不能读取接口原文，回到正常页面提取。

### B. 独立 HTTP 客户端：带会话小样本验证后启用

随附 `huaban_tools.py fetch` 只对已列入白名单的花瓣 HTTPS GET 路径发请求，无自动重试。可以从用户已提供、或当前工具明确允许导出的本地会话文件读取 `Cookie` 和其他必要请求头；不从浏览器配置文件、钥匙串或磁盘 Cookie 数据库取凭据，不要求用户在聊天中粘贴 Cookie。

```sh
python3 scripts/huaban_tools.py fetch \
  --url 'https://huaban.com/v3/pins/6498350553/similar?limit=3' \
  --headers-file /absolute/private/huaban-headers.json \
  --output response.json
```

`--headers-file` 是 HTTP 头名称到字符串的 JSON 对象，可包含 `Cookie`、`Authorization`、`User-Agent` 等；`--cookie-file` 也支持单独的 Cookie header 文本。文件留在仓库外，权限建议 600，不把值写入 skill、报告或日志。只授权向 `huaban.com` 发送，重定向不跟随。

从公开 `_app-e51013cb36f56ecd.js` 观察到请求包装模块 `33758`：调用模块 `7364.iD` 取得访问 token，条件性添加 `authorization: Bearer ...`；实验匹配时添加 `guest-token`、`x-guest-token`、`x-abtest-tags`。因此不能承诺“一个 Cookie 就够”。没有对全部端点做签名排查，也没有证明需要自行生成加密参数。

403、405、429、登录/JS 校验 HTML、非预期 JSON 都属于失败或需切换路径；按服务端明确的重试时间处理，普通重试仍失败就回浏览器。HTTP 200 不等于业务成功。脚本会验证响应结构，业务错误 JSON 不作为候选输出。

### C. 浏览器执行 JavaScript / 监听响应：按实际工具能力选择

完整 Playwright 的 `page.evaluate()` 可在页面上下文执行代码；CDP 的对应基础能力是 `Runtime.evaluate`。这与无障碍树是两回事。浏览器同源请求可以自动带 Cookie，但 `fetch(..., {credentials:'include'})` 不会自动获得应用额外添加的 Bearer、签名头或拦截器逻辑。

优先让页面正常触发请求，再通过工具正式支持的响应监听获取 JSON，这样沿用应用自身的请求逻辑。跨域凭据仍受 Cookie 属性和 CORS 约束，执行浏览器 JS 不是万能通行证。

以下仅是**完整 Playwright 环境**的示例，不适用于限制为只读 DOM 的浏览器 MCP evaluate：

```javascript
// page 已指向已登录的 https://huaban.com 页面；URL 来自刚观察的请求。
const result = await page.evaluate(async (url) => {
  const response = await fetch(url, { credentials: 'include' });
  const type = response.headers.get('content-type') || '';
  if (!response.ok || !type.includes('json')) throw new Error('未得到成功 JSON');
  const data = await response.json();
  if (!Array.isArray(data.pins)) throw new Error('不是候选图片响应');
  return data;
}, 'https://huaban.com/v3/pins/6498350553/similar?limit=3');
```

参考：[Playwright 页面执行](https://playwright.dev/docs/evaluating)、[网络监听](https://playwright.dev/docs/network)、[会话状态](https://playwright.dev/docs/auth)、[无障碍快照](https://playwright.dev/docs/aria-snapshots)。

## 定位原则

交互采用语义定位，批量数据提取采用稳定业务属性；两者均可运行在无头浏览器内。

- **点击与填表**：优先按角色加可访问名称定位，例如 `page.getByRole('textbox', { name: '搜索花瓣内容', exact: true })`。表单有可靠 label 时可用 `getByLabel`；没有 label 但有稳定 placeholder 时可用 `getByPlaceholder`。这不是所有场景都固定为 Role → Label → Placeholder 的排序。
- **核对实际语义**：“推荐画板”等文字不一定属于 button；先观察页面，再选择 role、label 或可见文字。交互目标有多个匹配时，通过容器范围或名称消歧，不用任意 `.first()` 点击。
- **图片列表**：当前 `a[href*="/pins/"] img` 依赖采集链接业务路径与图片标签，未依赖混淆 CSS 类或长层级链。它用于建立图片与 pin_id 的关系；可继续使用。部分图片缺少有效 alt，或被标为展示用途，机械改成 role=img 或按图片名称定位可能遗漏候选。
- **取数与缓存**：继续监听网页 JSON 与图片响应，并保留 DOM 作为字段补充；不因采用语义定位而增加逐图点击、整页 AX 快照或逐张截图。

`getByRole` 是按 ARIA 语义查询的定位器。本机 Playwright 1.61.0 的角色选择器源码会遍历 DOM，计算角色与可访问名称；它不是简单读取浏览器原生 AX 树，也不属于“所有定位器都位于 DOM 外部”的机制。无需单独开启“无障碍模式”。角色或可访问名称变化时，定位同样需要更新。

支持 open Shadow DOM 也不是 Role 定位独有：Playwright 定位器通常支持开放影子树，XPath 除外；closed-mode shadow roots 不受支持。不把 Role 定位描述为通用的 CSP 或访问限制解决方案。

依据：[官方定位器文档](https://playwright.dev/docs/locators)、[官方最佳实践](https://playwright.dev/docs/best-practices)、[Playwright 1.61.0 角色选择器源码](https://raw.githubusercontent.com/microsoft/playwright/v1.61.0/packages/injected/src/roleSelectorEngine.ts)。

## 图片与去重

API 的 `file.url` 含 `auth_key`。DOM 的 `img.currentSrc/src` 可能另有 `_fw240webp` 等缩略图后缀。下载时使用取得的完整地址；去重时可以提取不含变换后缀的 file key，但不能据此构造另一张可访问的原图 URL。地址可能过期，刷新来源即可重新获取。

`file.width/height` 只说明 API 对此文件给出的像素尺寸，不代表已取得可印刷源文件。`img.naturalWidth/naturalHeight` 仅是当前预览尺寸。`copyright` 数字枚举没有在本次确认授权含义，原样保存为线索，不映射成“可 POD 商用”。

直连或跨站嵌图失败时，优先缓存浏览器已显示的预览。当前 Chrome 按 Shift 右键可显示原生“图片存储为…”菜单；实测已保存两张 WebP 缩略图。`downloadMedia` 曾产生实际下载但调用仍超时，不能用超时简单断言文件未生成。按当前工具能力确认保存结果与准确路径，再写 `local_image` 交给 gallery 命令复制资源。页面资源导出能力在本次未列出目标图片，不将其作为已验证方案。
