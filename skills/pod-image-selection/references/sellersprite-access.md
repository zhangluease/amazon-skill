# 卖家精灵浏览器授权与取数

唯一必填输入是关键词，例如 `pet house`，固定美国站 Amazon.com。默认用专用持久 Chromium 正常运行原卖家精灵插件，由固定 Playwright 脚本提交关键词、读取公开商品表格。无需 HAR 文件或在聊天里提供凭据；不是 Agent 逐步截图点击。

## 默认入口

在 skill 目录运行，或使用实际脚本的绝对路径：

```sh
node scripts/sellersprite_browser.cjs search --query 'pet house' --out /absolute/run
```

入口内置依赖准备与检查。自动发现本机 Chrome/Edge 安装的原插件程序，复制程序文件到专用缓存；不读取日常浏览器的 Cookie、登录数据库或扩展账号存储。没有安装程序且没有缓存时返回 `EXTENSION_NOT_FOUND`；可用 `--extension-dir /absolute/installed/program` 指定用户已安装的插件程序目录。不要指向整个浏览器用户目录。浏览器、插件配置及并发锁见 [环境说明](environment.md)。

默认打开可见窗口，首次由用户正常登录/同步插件，完成后继续原查询。后续复用同一个专用 profile；原插件负责自己的会话维护。专用浏览器与日常浏览器各有登录状态，首次需要在专用浏览器再登录。不要直接接管日常 Chrome、导出密码或复制账号存储。

`--headless` 是可选模式；需要登录时在同一专用配置上切换到可见窗口。没有用户可操作的桌面就返回 `requires_interactive_browser`。脚本最多进入一次人工授权等待，默认300秒（`--authorization-timeout` 可设5–900秒），取消或超时后保留配置并结束。查询过程中首次明确失效可授权后重试同一关键词一次，再次失效停止。Agent 收到 `awaiting_login` 必须即时通知用户，并保持当前执行会话存活。

可单独运行 `check --out ...` 检查依赖与插件程序（不登录、不查询），或 `prepare --out ...` 准备依赖。它们的 `session_tested=false` 不能解释为已验证账号授权。`login --out ...` 只完成专用浏览器授权，不查询商品；用户普通提示词无需包含这些命令。

## 候选范围与排序

脚本打开 Amazon.com，在插件“产品查询”中切换为“关键词”，提交用户原词；确认新查询完成后读取表格。通过父体销量列识别数值，滚动收集虚拟表格；已显示加载数量却未读全时返回 `INCOMPLETE_TABLE_COLLECTION`。其他站点重定向、无法确认新结果或账号错误也按失败处理，不交付旧表格、零销量或伪造排名。

候选范围为**本次卖家精灵关键词查询加载的商品**，不是 Amazon 实时搜索页的全部商品。按父体预估近30天销量降序，ASIN去重、保留同父体不同变体、不相加父体销量，同销量按收集顺序。返回不足30个有效候选时如实交付。缺销量或缺图标记缺失；不以子体销量、BSR或评论数替代。

`ranking_scope=returned_candidates_only`、`global_top_30_verified=false`。界面未显示总匹配数则为未知；`1.2K` 等按界面数值换算并标记 `sales_precision=plugin_display`，`5K+` 这类下界不当精确销量使用。父体 ASIN 在界面不可见时保留未知，不从商品标题猜测。宽泛词可能包含非目标品类，不擅自改词或删掉高销量商品。

## 产物与失败反馈

任务目录包含 `browser-products.json`（公开表格字段）、`sellersprite-candidates.json`、`ranked-products.json`、`products.html`、`browser-status.json`、`authorization.json` 与环境检查。`normalize` 兼容保留的 `api-status.json` 是本地规范化元数据，不表示执行了独立接口请求。账号存储留在仓库外的专用 profile，不放进报告、仓库或试用包。

`browser-status.json` 记录失败类别、阶段与处理建议；`authorization.json` 记录 reused、awaiting_login、completed、timed_out、cancelled 或 requires_interactive_browser。不要因账号或站点问题反复重装依赖。反馈这些状态文件即可，不要求提供 profile 或凭据。

2026-11-13 已验证原插件5.0.6在专用 Chromium 中正常加载、展示登录界面；自建测试扩展验证了首次授权、进程重启后复用、失效恢复、取消、超时及无桌面处理。**原插件真实账号登录后的关键词流程尚未完成验证，需用户试用确认**。模拟测试不代表真实账号、长期续期或所有插件版本可用。

## 可选接口诊断

只有用户明确要求抓包或重放时才使用 [抓包说明](capture-plugin-requests.md)。历史验证的插件接口是 `GET https://e.sellersprite.com/v2/extension/competitor-lookup/US`，关键词参数 `qt=K&q=用户关键词`；`quick-view` 是预览接口。历史接口响应包含 `image`、`parent`、`units`，规范为 `image_url`、`parent_asin`、`parent_units_30d`。这些证据用于理解字段，不代表当前默认脚本调用接口。普通查询失败不自动回退到 HAR 导出。
