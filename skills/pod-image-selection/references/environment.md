# 环境与人工接管

普通竞品查询需要Node.js18+、Python3.9+标准库；直接运行search，由入口检查依赖。卖家精灵授权和花瓣采集按需准备package.json锁定版本的Playwright及浏览器。缺依赖只准备一次，账号或网站错误不反复重装。

## 卖家精灵会话

固定复用以下专用目录：

- macOS：`~/Library/Application Support/pod-image-selection/`
- Windows：`%LOCALAPPDATA%/pod-image-selection/`
- Linux：`$XDG_DATA_HOME/pod-image-selection/`，未设置时为 `~/.local/share/pod-image-selection/`

`http-session.json` 保存接口会话，`profile/` 保存专用浏览器授权，`extension/` 缓存原插件程序。通过 `--state-dir` 或 `POD_IMAGE_SELECTION_STATE_DIR` 可固定到其他私密目录；不按关键词重建，不放入Git、skill或任务产物，不打包给同事。

首次授权需要本机Chrome/Edge已安装原卖家精灵插件；发现失败时用 `--extension-dir` 指定插件版本程序目录。授权使用配套Chromium和专用profile。

收到 `seller_authorization / awaiting_login` 时，立即提示用户在打开的卖家精灵官网登录并保持窗口，继续等待同一进程。脚本同步插件账号、保存HTTP会话后关闭窗口并恢复原请求。默认等待300秒，可设 `--authorization-timeout 600`。普通查询无需先单独执行login。

取消、超时、拒绝或再次失效时停止。无可操作桌面返回 `requires_interactive_browser`；改在本机或用户可操作的远程桌面运行，xvfb本身不提供接管入口。`PROFILE_BUSY` 表示另一个任务在使用配置，等待其结束，不删除运行中的锁。私密会话不回显或上传；排障提供状态文件即可。

## 花瓣人机验证接管

花瓣先尝试匿名无头采集，可用Chrome或配套Chromium。明确真人验证触发后，默认打开一次可见会话，等待人工完成后继续。

收到 `verification.json / awaiting_human` 时立即通知用户，保持同一采集进程。默认等待300秒，可设 `--verification-timeout 600`。取消、超时、再次被拦分别报告cancelled、timed_out、blocked_again；不代做验证码。

无交互桌面时返回requires_interactive_browser。平台确有用户可操作的同一远程桌面时，卖家精灵可设 `POD_IMAGE_SELECTION_INTERACTIVE=1`，花瓣可设 `POD_HUABAN_INTERACTIVE=1`；这些设置不创建桌面。

## 独立排障

卖家精灵用 `sellersprite_browser.cjs check/prepare --out ...` 检查或准备授权环境；花瓣用 `environment.cjs check/prepare --out ...`。普通search/采集入口已包含所需检查。

`POD_HUABAN_PYTHON` 可指定Python；`POD_HUABAN_CACHE_DIR` 可指定运行库缓存。默认缓存目录名为pod-huaban-materials。check/prepare仅检查环境，不证明站点连接或账号有效。
