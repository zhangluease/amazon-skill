# 卖家精灵授权与接口取数

输入关键词，固定美国站，按父体近30天销量倒排取前30主图。默认独立 Python HTTP 请求，直接处理接口 JSON。普通查询不启动浏览器，不打开 Amazon，不填插件搜索框或读取表格。

## 普通入口

```sh
node scripts/sellersprite_browser.cjs search --query 'pet house' --out /absolute/run
```

保留原入口文件名方便更新；内部已改为 HTTP。Agent 使用实际脚本绝对路径，每次输出新目录。普通查询只检查可用 Python，读取仓库外专用目录里的 `http-session.json`；不先运行浏览器环境 check/login，不依赖 HAR。

接口为 `GET https://e.sellersprite.com/v2/extension/competitor-lookup/US`，参数 `qt=K&q=用户关键词`，客户端计算插件校验值并附加已授权会话。HTTP 状态、业务码、美国站标识和商品结构均需通过；HTTP200不代表查询成功。

只取 `asin`、`units`（父体近30天预估销量）、`image`（主图）。规范为 `asin`、`parent_units_30d`、`image_url`，另外保留编号、商品链接及来源元数据。ASIN去重，保留不同子体，不相加父体销量；缺销量或缺图不补零，少于30如实交付。不采集标题、价格、评分、评论数等额外商品指标。

仅排名本次返回候选：`ranking_scope=returned_candidates_only`、`global_top_30_verified=false`，不把总匹配数说成已取得数量。宽泛关键词可包含其他品类，不暗中改词或删掉高销量商品。

## 首次授权与失效恢复

只有缺失/损坏会话或明确 `ERR_NEED_AUTHORIZED`、`ERR_NEED_RE_AUTHORIZED`、`ERROR_UNAUTHORIZED` 才进入一次授权流程。限流、网络错误、其他业务失败不反复打开浏览器，不回退到 Amazon UI。

授权时按需准备锁定 Playwright、配套 Chromium，发现用户本机安装的原卖家精灵插件程序，运行于固定专用 profile；不读取日常 Chrome 的账号数据库。先在后台复用该专用配置中的原插件授权；需要人工时打开卖家精灵官网，由用户正常登录。脚本通过原插件内部消息同步同一专用配置的官网账号，取得刷新后的会话，并读取该专用插件已生成的必要设备字段。只访问已管理专用 profile，不扫描其他浏览器配置。

会话保存至专用根目录 `http-session.json`，目录权限700、文件权限600（支持这些权限的平台）。文件只含固定接口所需会话头、插件版本/语言等参数，不含密码、用户姓名或完整账号对象；它仍是私密授权文件，不上传、不发聊天、不放入报告或仓库。原插件程序不会随 skill 分发。

保存采用临时文件写入、刷盘和替换；完成保存后才关闭授权浏览器，再用独立 HTTP 继续同一关键词。后续会话有效时不启动浏览器。平台强制失效仍需重新登录，不能承诺永久有效。

Agent 收到 `seller_authorization / awaiting_login` 立即通知用户在官网登录并保持窗口打开，继续等待同一进程。默认300秒，可设 `--authorization-timeout 600`（5–900秒）。取消/超时/授权拒绝明确结束；无交互桌面返回 `requires_interactive_browser`。授权后原请求只恢复一次，再次失效停止。

`login --out ...` 仅用于用户明确要求单独准备/修复授权；普通搜索不要拆为 login→关闭→search 两次启动。`check`/`prepare` 仅供环境排障，不证明账号有效。`--headless` 为旧参数兼容，普通 HTTP 查询本来就无浏览器。

## 产物与验证边界

输出 `request-plan.json`（不含凭据）、公开字段 `sellersprite-candidates.json`、`ranked-products.json`、`products.html`、`api-status.json`、`authorization.json` 和兼容保留的 `browser-status.json`。最后一个记录 `collection_method=independent_http`，文件名不代表查询使用浏览器。仅授权时有浏览器环境报告。不要保存原始账号响应或会话头。

历史真实会话已验证 `pet house` 美国站请求成功，返回200候选与主图、父体销量。新版本测试覆盖私密会话校验、零浏览器普通查询、授权一次恢复、错误不回退 UI，以及模拟扩展的会话保存/重启复用/取消/超时/无桌面处理。**新增官网登录→原插件同步→独立HTTP的真实账号全流程尚待试用确认**，不能以模拟测试或历史 HAR 请求代替。

仅用户明确要求接口诊断时使用 [手动抓包说明](capture-plugin-requests.md)，不作为普通失败回退。
