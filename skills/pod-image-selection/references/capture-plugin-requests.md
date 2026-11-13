# 手动记录 Amazon 页面卖家精灵插件的请求

这是**用户明确要求接口诊断时**才使用的手动流程，不是普通选图所需步骤。默认入口已改为 [专用持久浏览器授权](sellersprite-access.md)，无需 HAR；查询失败不自动回退到导出会话。以下保留历史协议核对与本机诊断方法。

需要诊断时，用户先在已安装卖家精灵插件的本机浏览器登录/同步账号，再主动导出 HAR。只需在 Amazon 页面操作插件，不打开卖家精灵 Web 产品查询页面。独立 HTTP 诊断仅复用用户明确提供的文件，不自动导出或续期。

## 要找的接口

从本机已安装的 SellerSprite 5.0.6 程序中核对到以下调用链：

- `content/index.js` 的 `index-new`（产品查询）调用 `sale(marketplace, ASIN列表)`。
- 扩展消息 `SE.SALE` 在 `background/index.js` 路由到 `Wg.sales`。
- `Wg.sales` 使用 `GET /v2/extension/competitor-lookup/{站点}`，默认主机为 `https://e.sellersprite.com`，批量传 `asins` 与 `tk`；包装器再附加版本、语言、会话头等。
- `quick-view/{站点}` 对应页面商品卡片的快速预览，不是产品查询大表的主接口。截图可能同时用到多个接口；部分商品信息也来自 Amazon 页面。

当前截图为日本站，因此主接口是 `https://e.sellersprite.com/v2/extension/competitor-lookup/JP`。美国站将站点部分换为 `US`。返回结构为 `data.items`，其中 `units` 为父体销量、`asin`/`parent` 为商品/父体 ASIN，主图字段为 `image`。2026-11-13 已用同步后的新 HAR 成功独立重放；随后固定美国站、传 `qt=K&q=pet+house` 的关键词请求也返回 `OK`、200条商品和主图，总匹配数7519。插件返回上限与全局排序边界见 [接口说明](sellersprite-access.md)。

## Chrome 中如何导出

1. 保持 Amazon 页面与插件处于登录状态。在地址栏打开 `chrome://extensions`，找到卖家精灵。看不到调试入口时，打开右上角“开发者模式”。
2. 点击卖家精灵卡片中的 **Service Worker / 服务工作线程 / 检查视图** 链接，打开扩展后台 DevTools。不能只打开 Amazon 网页自己的开发者工具，因为请求由扩展后台发送。入口见 [Chrome 官方说明](https://developer.chrome.com/docs/extensions/get-started/tutorial/debug#debug_the_service_worker)。
3. 在这个 DevTools 窗口选择 **Network / 网络**，确认正在记录，清空旧记录，过滤框填 `competitor-lookup`。
4. 回到 Amazon 页面，点击卖家精灵悬浮按钮，进入产品查询大表，点击左下角“刷新”。保持后台 DevTools 窗口打开，直到商品销量出现；不需要退出登录、重新安装插件或登录 Amazon 买家账号来做本次抓包。
5. 回到 Network。应能看到 `JP?asins=...` 或 `US?asins=...` 等请求。选中它，检查 Headers 的 URL、Payload 的参数、Response 的业务码与 `data.items`。HTTP 200 本身不能证明成功。
6. 在请求列表右键，选择 **Save all listed as HAR (sanitized)**；也可点工具栏 **Export HAR (sanitized)**。保存到本机，例如 `~/Downloads/sellersprite-raw.har`。[导出方式](https://developer.chrome.com/docs/devtools/network/reference#save-all-network-requests-to-a-har-file)

默认 sanitized 导出会去掉 Cookie、Set-Cookie、Authorization 等标准头，但不应假定插件自定义的 Auth-Token、Auth-FP 也已删除。不要上传或粘贴原始 HAR/cURL；先留在本机。

## 不需要手工删除凭据

用户只需告诉 Agent 原始 HAR 的本地路径。Agent 在本机运行以下脚本，输出必要的接口诊断 JSON，不回显原文件。脚本只保留卖家精灵目标接口，所有请求头仅保留名称；Cookie 和未知查询参数值不复制，响应仅提取业务码、字段名、少量公开商品数字与 ASIN，图片只记录主机。

```sh
python3 scripts/inspect_sellersprite_har.py /absolute/private/capture.har --output /absolute/output/sellersprite-requests.safe.json
```

命令在 skill 目录运行；其他用户将输入/输出换成自己的绝对路径。Windows 可使用当前可用的 Python 3.9+。输出不能覆盖原 HAR。如果脚本摘要 `matching_requests=0`，说明目标请求没有记录到；检查是否在扩展的后台 Network 面板，并再次点击插件“刷新”。如果 `body_available=false`，先保留现有文件，说明缺少响应体，不编造截图数据。

该副本足够分析路径、参数结构和返回字段，不能直接复用鉴权，因为凭据已删除。需要验证带会话 HTTP 重放时另行在本机处理，不要求用户把凭据发到聊天中。

2026-11-13 旧会话的一次本机重放返回 `ERR_NEED_RE_AUTHORIZED`，用户确认插件内也要求重新登录。用户同步账号并重新导出后独立请求成功。不能把 HTTP 200 当作重放成功，也不能保证会话长期有效。授权失败时先对照插件内是否仍能取得销量；插件也要求登录时由用户同步账号、刷新数据并重新导出。浏览器登录态改变不会自动更新旧 HAR；不循环重试旧会话。

用户已提供原 HAR 并授权使用其中会话取数时，可运行 `scripts/sellersprite_plugin_client.py search --query 'pet house' --session-har 本机原HAR路径 --out 任务目录`。也可将路径配置到 `SELLERSPRITE_SESSION_HAR`；不用在提示词中重复。原 HAR 保持在仓库外，仅向固定卖家精灵 HTTPS 主机发送必要会话头，不导出或回显凭据。
