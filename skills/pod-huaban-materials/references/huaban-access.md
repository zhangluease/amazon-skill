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

### A. 已登录浏览器正常页面：默认可用路径

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

## 图片与去重

API 的 `file.url` 含 `auth_key`。DOM 的 `img.currentSrc/src` 可能另有 `_fw240webp` 等缩略图后缀。下载时使用取得的完整地址；去重时可以提取不含变换后缀的 file key，但不能据此构造另一张可访问的原图 URL。地址可能过期，刷新来源即可重新获取。

`file.width/height` 只说明 API 对此文件给出的像素尺寸，不代表已取得可印刷源文件。`img.naturalWidth/naturalHeight` 仅是当前预览尺寸。`copyright` 数字枚举没有在本次确认授权含义，原样保存为线索，不映射成“可 POD 商用”。

直连或跨站嵌图失败时，优先缓存浏览器已显示的预览。当前 Chrome 按 Shift 右键可显示原生“图片存储为…”菜单；实测已保存两张 WebP 缩略图。`downloadMedia` 曾产生实际下载但调用仍超时，不能用超时简单断言文件未生成。按当前工具能力确认保存结果与准确路径，再写 `local_image` 交给 gallery 命令复制资源。页面资源导出能力在本次未列出目标图片，不将其作为已验证方案。
