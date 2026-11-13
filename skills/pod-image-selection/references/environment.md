# 环境检查与会话目录

普通竞品查询需要 Node.js18+、Python3.9+（标准库），先匿名HTTP请求Amazon搜索页提取ASIN，再通过卖家精灵ASIN接口查销量和主图，不依赖浏览器或HAR。直接运行search，不先独立check/login。原插件、Playwright和Chromium只在卖家精灵首次授权、明确会话失效或环境排障时准备。花瓣阶段按原方式按需准备浏览器。

Amazon请求无登录、无Cookie，默认1页，可显式 `--amazon-pages 2`，最多4页/200个去重ASIN。遇验证、错误或无法确认搜索结果就停止，不自动开Amazon浏览器，也不重新登录卖家精灵解决Amazon错误。卖家精灵授权恢复沿用已保存的 `amazon-search.json`，不重复发起Amazon搜索。

## 本机私密会话

默认根目录：

- macOS：`~/Library/Application Support/pod-image-selection/`
- Windows：`%LOCALAPPDATA%\\pod-image-selection\\`
- Linux：`$XDG_DATA_HOME/pod-image-selection/`，未设置时 `~/.local/share/pod-image-selection/`

其中 `http-session.json` 供独立 HTTP 使用，`profile/` 保留专用浏览器的网页/插件授权，`extension/` 为原插件程序缓存。会话目录不放入 Git、skill 或输出目录，也不打包给同事。可用 `--state-dir` 或 `POD_IMAGE_SELECTION_STATE_DIR` 固定到其他独立私密目录；不要每个关键词换目录。

目录由专属标记管理，并发锁防止同时授权或更新。不要删除运行中锁。首次只有空目录或已管理目录可用，不允许把日常 Chrome/Edge 用户目录当作专用配置。目录权限700、会话文件600（支持的平台）；Windows按当前用户权限保管。

HTTP会话只含必要授权字段，不能上传或显示内容。反馈 `browser-status.json`、`api-status.json`、`authorization.json` 即可，不提供 profile 或 http-session.json。私密文件损坏/过期时由正常授权恢复一次，其他网络或业务失败不反复登录。

## 按需浏览器授权

使用原插件程序和配套 Chromium。自动从本机 Chrome/Edge 的插件程序目录发现；找不到时安装原插件或用 `--extension-dir` 指定已安装的版本程序目录。仅缓存程序，不复制日常浏览器账号存储。仓库不包含第三方插件代码。

授权先在后台检查专用 profile 的插件账号，必要时打开卖家精灵官网。用户正常登录，脚本在该专用原插件内同步账号、读取必要设备字段，保存私密 HTTP 会话后才关闭窗口并继续请求。不要打开 Amazon、操作其搜索页或导出表格来取得候选。

看到 `awaiting_login` 立即通知用户保持窗口打开，继续等待同一执行进程。默认300秒，可用 `--authorization-timeout 600`。关闭、超时、拒绝或再次失效停止。无用户可操作的桌面返回 `requires_interactive_browser`；需要本机或用户可访问的远程桌面，xvfb本身不提供接管入口。`POD_IMAGE_SELECTION_INTERACTIVE=1` 只用于已经确认的同一可交互远程桌面，不创建桌面或绕过验证。

单独 `sellersprite_browser.cjs check --out ...` 检查依赖/插件，`prepare` 可准备依赖；二者不查询、不证明登录有效。用户明确要求时可单独 `login --out ...`，普通 search 已包含授权恢复。

Playwright版本锁定在 package.json。复用已有匹配版本，或安装至技能私有缓存，不改全局 npm/PATH。缓存沿用旧名 pod-huaban-materials，避免更名后重复下载：

- macOS：`~/Library/Caches/pod-huaban-materials/playwright-<版本>/`
- Windows：`%LOCALAPPDATA%\\pod-huaban-materials\\playwright-<版本>\\`
- Linux：`$XDG_CACHE_HOME/pod-huaban-materials/playwright-<版本>/`，默认 `~/.cache/...`

`POD_HUABAN_CACHE_DIR` 可指定运行库缓存，`POD_HUABAN_PYTHON` 可指定Python。缺Node/Python需安装；依赖自动准备最多一次，网站/账号错误不反复重装。

## 花瓣人机验证接管

花瓣匿名采集仍用 `huaban_browser.cjs`，自动检查并准备依赖，可用Chrome或配套Chromium；不沿用卖家精灵会话。诊断入口：

```sh
node scripts/environment.cjs check --out /absolute/output/environment
node scripts/environment.cjs prepare --out /absolute/output/environment
node scripts/environment.cjs smoke --query '狗 动漫' --limit 3 --out /absolute/output/smoke
```

环境报告ready只证明本地可运行，不证明站点网络、授权或人工接管可用。smoke访问真实花瓣，普通查询不用额外先执行。

默认 `--human-verification auto`；明确真人验证出现时本机打开一次可见浏览器，在内存中迁移本轮隔离状态，人工完成后继续同一可见会话。采集进程写 `verification.json`；Agent看到 `awaiting_human` 立即提示并保持进程，不等超时才通知。默认300秒，可设 `--verification-timeout 600`（30–900秒）。不代做验证码，不将验证Cookie保存到报告。

取消、超时、再次被拦分别记录cancelled、timed_out、blocked_again。容器、SSH、CI、xvfb或无法确认桌面时返回requires_interactive_browser；`POD_HUABAN_INTERACTIVE=1` 仅用于已经确认的可操作远程桌面。普通HTTP403/405/429不自动判定验证码。详细采集见 [花瓣流程](huaban-workflow.md)。

## 试用提示词

安装时只需说“请安装我提供的 pod-image-selection skill。”，正常使用：

```text
使用 $pod-image-selection，关键词：pet house。
```

首次与后续提示词相同。Agent内部处理环境、会话与授权，仅需真人登录时即时提示；只安装不主动查询。
