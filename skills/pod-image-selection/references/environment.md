# 环境与人工接管

普通竞品查询需要Node.js18+、Python3.9+标准库；直接运行search，由入口检查依赖。卖家精灵授权和花瓣采集按需准备package.json锁定版本的Playwright及浏览器。缺依赖只准备一次，账号或网站错误不反复重装。

Playwright 浏览器的下载和启动共用 `~/.amazon-skill/config.json` 的 `browser.cache_dir`，与日本站关键词技能使用同一规则。未配置时读取绝对路径 `PLAYWRIGHT_BROWSERS_PATH`，或使用当前系统用户的 `ms-playwright` 默认缓存。不同 Playwright 版本可在该目录保存各自需要的浏览器，在版本兼容时复用。

## 卖家精灵会话

固定复用以下专用目录：

- macOS：`~/Library/Application Support/pod-image-selection/`
- Windows：`%LOCALAPPDATA%/pod-image-selection/`
- Linux：`$XDG_DATA_HOME/pod-image-selection/`，未设置时为 `~/.local/share/pod-image-selection/`

`http-session.json` 保存接口会话，`profile/` 保存专用浏览器授权，`extension/` 缓存原插件程序。通过 `--state-dir` 或 `POD_IMAGE_SELECTION_STATE_DIR` 可固定到其他私密目录；不按关键词重建，不放入Git、skill或任务产物，不打包给同事。

首次授权需要本机Chrome/Edge已安装原卖家精灵插件；发现失败时用 `--extension-dir` 指定插件版本程序目录。授权使用配套Chromium和专用profile。

收到 `seller_authorization / awaiting_login` 时，立即提示用户在打开的卖家精灵官网登录并保持窗口，继续等待同一进程。脚本同步插件账号、保存HTTP会话后关闭窗口并恢复原请求。默认等待300秒，可设 `--authorization-timeout 600`。普通查询无需先单独执行login。

取消、超时、拒绝或再次失效时停止。无可操作桌面返回 `requires_interactive_browser`；改在本机或用户可操作的远程桌面运行，xvfb本身不提供接管入口。`PROFILE_BUSY` 表示另一个任务在使用配置，等待其结束，不删除运行中的锁。私密会话不回显或上传；排障提供状态文件即可。

## 花瓣登录会话

以图搜图上传需要花瓣登录，入口自动复用上述专用目录中的 `huaban-state.json`。缺失或失效时，运行 `node scripts/huaban_login.cjs`，立即提示用户在打开的可见浏览器中点击“登录/注册”并完成登录，继续等待同一进程。默认等待600秒，可设 `--timeout 900`；保存后重跑原图片检索。用户取消或超时即停止，不反复弹窗。

登录脚本使用隔离会话，只保存花瓣域的storageState，按私密文件保存在当前系统用户的专用目录；不读取日常浏览器配置，不放入Git、skill或任务产物。`--state /private/huaban-state.json` 可指定另一个用户授权的文件。花瓣会话与卖家精灵的 `http-session.json` 独立，公开的采集状态文件不写入会话凭据。

## 花瓣人机验证接管

花瓣先尝试匿名无头采集，可用Chrome或配套Chromium。明确真人验证触发后，默认打开一次可见会话，等待人工完成后继续。

收到 `verification.json / awaiting_human` 时立即通知用户，保持同一采集进程。默认等待300秒，可设 `--verification-timeout 600`。取消、超时、再次被拦分别报告cancelled、timed_out、blocked_again；不代做验证码。

无交互桌面时返回requires_interactive_browser。平台确有用户可操作的同一远程桌面时，卖家精灵可设 `POD_IMAGE_SELECTION_INTERACTIVE=1`，花瓣可设 `POD_HUABAN_INTERACTIVE=1`；这些设置不创建桌面。

## 独立排障

浏览器收尾只针对脚本自己启动的实例：先等待各个 `BrowserContext.close()` 完成，再调用 `Browser.close()`；正常完成和进入 `finally` 的失败路径使用同一收尾逻辑。卖家精灵的持久化授权窗口使用 `context.close()`。复用用户浏览器制作 PODQHY 效果图时，交付后保留当前设计页。不要用 `killall`、`pkill`、强制结束进程或修改浏览器“正常退出”标记来隐藏恢复提示。外部强制终止、驱动崩溃或系统关机可能使收尾无法完成，不能保证所有退出方式都不会触发恢复提示。

卖家精灵用 `sellersprite_browser.cjs check/prepare --out ...` 检查或准备授权环境；花瓣用 `environment.cjs check/prepare --out ...`。普通search/采集入口已包含所需检查。

`POD_HUABAN_PYTHON` 可指定Python；`POD_HUABAN_CACHE_DIR` 可指定运行库缓存。默认缓存目录名为pod-huaban-materials。check/prepare仅检查环境，不证明站点连接或账号有效。

排查登录窗口没有出现时，可在本机桌面执行 `node scripts/environment.cjs check --headed --out "RUN/environment"`。需要补齐依赖时使用 `prepare --headed`。卖家精灵插件授权可加 `--browser chromium` 检查其指定的浏览器。输出中的 `checks.browser_config` 是实际缓存路径，`checks.browser` 记录启动测试。
