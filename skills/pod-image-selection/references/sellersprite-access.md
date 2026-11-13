# 卖家精灵授权与接口取数

输入关键词，固定美国站。先匿名HTTP取得Amazon搜索结果ASIN，再批量向卖家精灵查询父体近30天销量和主图，倒排取前30。普通查询不启动浏览器，不操作插件搜索框或表格；仍会发起正常HTTP网页/接口请求。

## 普通入口

```sh
node scripts/sellersprite_browser.cjs search --query 'pet house' --out /absolute/run
```

保留原入口文件名方便更新；内部为HTTP流水线。Agent使用实际脚本绝对路径，每次输出新目录。先运行随附 `amazon_search.py`，保存 `amazon-search.json`，再读取仓库外的 `http-session.json` 查询卖家精灵。不先独立check/login，不依赖HAR。

## Amazon匿名搜索

入口为 `GET https://www.amazon.com/s?k=关键词&page=1`，不带Cookie或账号，返回HTML。只提取主卡片 `data-component-type="s-search-result"` 的有效 `data-asin`，不把推荐轮播或任意链接中的ASIN混入。保存页面号、URL、时间、响应摘要与ASIN清单，不保存原始HTML或匿名会话标识。

默认第一页全部去重主卡片，包含该类型卡片中实际返回的广告。用户明确要更多时可 `--amazon-pages 2`，最多4页且不超过200个去重ASIN；检测无下一页后停止。不是全市场销量前30。匿名默认配送上下文、广告和实时排序可能与已登录浏览器不同，不保证重现用户当时的结果。

HTTP错误、验证码、查询词不符或无法确认结果时失败，不把它当作零结果，不自动开浏览器、循环重试、登录Amazon或改用卖家精灵关键词库。只有明确搜索页无结果证据才返回empty，此时不调用或授权卖家精灵。匿名不等于免风控，单次成功不代表所有IP和时间可用。

## 按ASIN批量查询

接口为 `GET https://e.sellersprite.com/v2/extension/competitor-lookup/US`，参数 `asins=本次搜索ASIN清单`；客户端计算该清单的插件校验值，附加已授权会话。**不传qt=K或q=关键词**，后者是卖家精灵关键词库另一候选范围。HTTP状态、业务码、美国站标识和商品结构均需通过。

只取 `asin`、`units`（父体近30天预估销量）、`image`（主图）。规范为 `asin`、`parent_units_30d`、`image_url`，另外保留编号、商品链接及来源元数据。ASIN去重，保留不同子体，不相加父体销量；缺销量或缺图不补零，少于30如实交付。不采集标题、价格、评分、评论数等额外商品指标。

仅排名这次取得的Amazon搜索ASIN：`candidate_source=amazon_anonymous_search_http`、`ranking_scope=returned_candidates_only`、`global_top_30_verified=false`。接口多返回的其他ASIN排除，未返回的请求ASIN标记missing，不补零；同销量按Amazon卡片顺序。`total_matches`未知，不把卖家精灵批量返回数当成Amazon总搜索匹配数。

## 首次授权与失效恢复

只有缺失/损坏会话或明确 `ERR_NEED_AUTHORIZED`、`ERR_NEED_RE_AUTHORIZED`、`ERROR_UNAUTHORIZED` 才进入一次授权流程。限流、网络错误、其他业务失败不反复打开浏览器，不回退到 Amazon UI。

授权时按需准备锁定 Playwright、配套 Chromium，发现用户本机安装的原卖家精灵插件程序，运行于固定专用 profile；不读取日常 Chrome 的账号数据库。先在后台复用该专用配置中的原插件授权；需要人工时打开卖家精灵官网，由用户正常登录。脚本通过原插件内部消息同步同一专用配置的官网账号，取得刷新后的会话，并读取该专用插件已生成的必要设备字段。只访问已管理专用 profile，不扫描其他浏览器配置。

会话保存至专用根目录 `http-session.json`，目录权限700、文件权限600（支持这些权限的平台）。文件只含固定接口所需会话头、插件版本/语言等参数，不含密码、用户姓名或完整账号对象；它仍是私密授权文件，不上传、不发聊天、不放入报告或仓库。原插件程序不会随 skill 分发。

保存采用临时文件写入、刷盘和替换；保存后才关闭授权浏览器，用独立HTTP继续同一ASIN清单，**不再次请求Amazon搜索**。后续会话有效时不启动浏览器。平台强制失效仍需重新登录，不能承诺永久有效。

Agent 收到 `seller_authorization / awaiting_login` 立即通知用户在官网登录并保持窗口打开，继续等待同一进程。默认300秒，可设 `--authorization-timeout 600`（5–900秒）。取消/超时/授权拒绝明确结束；无交互桌面返回 `requires_interactive_browser`。授权后原请求只恢复一次，再次失效停止。

`login --out ...` 仅用于用户明确要求单独准备/修复授权；普通搜索不要拆为 login→关闭→search 两次启动。`check`/`prepare` 仅供环境排障，不证明账号有效。`--headless` 为旧参数兼容，普通 HTTP 查询本来就无浏览器。

## 产物与验证边界

输出 `amazon-search.json`、`request-plan.json`（ASIN参数，无凭据）、公开字段 `sellersprite-candidates.json`、`ranked-products.json`、`products.html`、`api-status.json`、`authorization.json` 和兼容保留的 `browser-status.json`。最后一个记录 `collection_method=independent_http`，文件名不代表查询使用浏览器。仅授权时有浏览器环境报告。不要保存原始账号响应或会话头。

2026-11-13真实匿名HTTP两次读取美国站pet house第一页，均取得48个ASIN，约2.5–2.6秒，无登录/Cookie/浏览器。原插件程序及历史HAR已验证ASIN批量参数。新增测试覆盖匿名页面解析、验证/错误/无数据区分、分页上限、固定US的ASIN参数、排除未请求商品、缺销量以及授权时沿用同一快照。**新增匿名搜索→US批量销量与自动授权的真实账号全流程仍待试用确认**；历史US关键词接口成功不替代新候选范围验证。

仅用户明确要求接口诊断时使用 [手动抓包说明](capture-plugin-requests.md)，不作为普通失败回退。
