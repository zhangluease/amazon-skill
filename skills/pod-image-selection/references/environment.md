# 环境自检与首次试用

竞品查询与花瓣阶段都需要 Node.js 18+、Python 3.9+ 和 skill 的 package.json 锁定版本 Playwright。卖家精灵使用配套 Chromium 加载原插件；花瓣可用本机 Chrome 或配套 Chromium。小伙伴只需安装完整 skill 文件夹并提供关键词，Agent 自动检查和按需准备依赖；首次与后续使用的提示词相同，无需描述环境准备步骤。

卖家精灵入口为 `node scripts/sellersprite_browser.cjs search --query 'pet house' --out /absolute/run`，内置环境检查、插件发现、授权与查询。首次需要本机已安装的卖家精灵原插件程序，以及用户可操作的桌面，正常登录/同步专用浏览器中的插件；后续复用专用配置，无需 HAR。缺依赖时准备一次，账号失效时处理登录，不反复重装。

## 卖家精灵的专用浏览器配置

配置、账号存储和原插件程序缓存保存在仓库与输出目录外：

- macOS：`~/Library/Application Support/pod-image-selection/`
- Windows：`%LOCALAPPDATA%\pod-image-selection\`
- Linux：`$XDG_DATA_HOME/pod-image-selection/`，未设置时为 `~/.local/share/pod-image-selection/`

可用 `POD_IMAGE_SELECTION_STATE_DIR` 或 `--state-dir` 指定独立私密目录；不能指向日常浏览器用户目录、Git仓库、skill目录或任务产物目录。新目录应为空，已管理目录有专属标记和运行锁；并发使用同一配置会返回 `PROFILE_BUSY`。不要删除运行中浏览器的锁，也不要把 profile 打包或同步给同事。每位同事使用自己的专用配置和账号。

首次自动寻找 Chrome/Edge 的原插件程序，保留插件公共标识，不复制日常账号数据。找不到时提示安装，或用 `--extension-dir` 指定插件版本程序目录；仓库和试用包不包含第三方插件程序。自动化扩展使用配套 Chromium；不要换成日常 Chrome 的默认 profile。

默认有窗口，可选 `--headless`。出现 `seller_authorization` / `awaiting_login` 事件时立即提示用户，保持同一执行进程，等待完成登录/验证后继续；默认300秒，可设 `--authorization-timeout 600`。关闭窗口、超时、再次失效时停止，专用配置保留。无桌面的容器或SSH环境返回 `requires_interactive_browser`，需要转到本机或用户能操作的远程桌面。`POD_IMAGE_SELECTION_INTERACTIVE=1` 仅用于已确认同一可交互远程桌面的情况，不会创建桌面或跳过验证。

`sellersprite_browser.cjs check --out ...` 只检查当前依赖和插件程序，`prepare --out ...` 可准备依赖，二者均不证明登录有效。`login --out ...` 只完成正常浏览器授权。普通 `search` 已包含必要步骤，用户不用逐条运行。

## 花瓣环境入口与结果

在 skill 目录运行下列命令，或把 scripts 路径改成实际绝对路径。`--out` 指向用户当前项目中可写的目录，不依赖原开发者的用户名、工作目录或父目录 npm 包。

| 命令 | 作用 | 是否访问花瓣 |
| --- | --- | --- |
| `node scripts/environment.cjs check --out /absolute/output/environment` | 检查 Node、Python、npm、Playwright 版本、输出写入，并实际启动浏览器 | 否 |
| `node scripts/environment.cjs prepare --out /absolute/output/environment` | 同上；缺依赖时按需准备，再检查 | 否；必要时访问 npm 和官方浏览器下载源 |
| `node scripts/environment.cjs smoke --query '狗 动漫' --limit 3 --out /absolute/output/smoke` | 小样本验证匿名搜索、数据读取与本地预览缓存 | 是 |

`check` / `prepare` 成功退出 0，环境未就绪退出 2。`smoke` 环境失败退出 2，网站或采集链路失败退出 3。输出 JSON 的 `checks` 指明失败项与 remedy；不要依赖终端最后一行“命令执行完成”判断成功。

`environment.json` 的 `ready=true` 只说明自动采集环境可运行；不代表花瓣网络、账号权限或真人接管桌面可用。`checks.human_handoff` 另记可交互桌面判断，不影响无头采集就绪状态。`smoke.json` 另记真实联网结果。普通采集命令内置同样的检查和按需准备，并复用检查时启动的浏览器；遇到真人验证时才切换到可见浏览器。

## 依赖如何准备

先寻找 skill 可用的锁定版本 Playwright，再寻找本技能私有缓存。已有其他版本时，不升级或卸载全局包，按需另装锁定版本。私有运行库默认位置：

- macOS：`~/Library/Caches/pod-huaban-materials/playwright-<版本>/`
- Linux：`$XDG_CACHE_HOME/pod-huaban-materials/playwright-<版本>/`，未设置时为 `~/.cache/...`
- Windows：`%LOCALAPPDATA%\pod-huaban-materials\playwright-<版本>\`

可通过 `POD_HUABAN_CACHE_DIR` 指定缓存根目录；通过 `POD_HUABAN_PYTHON` 指定 Python 可执行文件路径。解释器自动尝试 python3、python、py -3，选择满足版本的一个，报告完整可用调用方式。

花瓣优先实际启动 Chrome；没有可用 Chrome 时尝试已安装的匹配版 Chromium，必要时调用锁定版 Playwright CLI 准备 Chromium。卖家精灵始终检查配套 Chromium，以便正常加载扩展。浏览器二进制使用 Playwright 官方缓存机制。花瓣可用 `--browser chromium` 验证管理版浏览器，或 `--browser chrome` 明确只选 Chrome。

单次运行最多准备一次 Playwright、一次 Chromium，失败保留阶段与处理建议；不修改系统 PATH、全局 npm 包、日常浏览器配置或账号登录态。

## 失败如何处理

| 检查项 | 典型问题 | 处理 |
| --- | --- | --- |
| node | 不存在或低于 18 | 安装 Node.js LTS；重新打开 Agent 使 PATH 生效 |
| python | 不存在或低于 3.9 | 安装受支持 Python，或指定 POD_HUABAN_PYTHON |
| npm | 不可调用 | 若已有可用 Playwright，不阻断采集；需要安装运行库时修复 Node 附带 npm |
| playwright | 缺失、版本不匹配或安装不完整 | 运行 prepare，安装到本技能私有缓存 |
| install | npm 安装失败 | 检查软件源、代理和网络，不把原始凭据或安装日志回显到报告 |
| browser | 找不到浏览器或无法启动 | prepare 后重查；Linux 缺系统共享库、企业执行策略等需单独处理 |
| output | 没有写入权限或空间不足 | 指定可写输出目录；检查磁盘空间与权限 |
| smoke | 403/405/429、登录提示、验证码、网络超时 | 本地依赖已通过时，按站点访问问题处理，不反复重装依赖 |

花瓣阶段首次先尝试匿名公开内容；出现明确登录要求时才交由用户在可见浏览器登录。匿名花瓣检索与卖家精灵的浏览器登录授权是两个独立阶段，不能把卖家精灵默认设为匿名。不要要求用户在聊天里粘贴 Cookie 或密码。

## 花瓣人机验证接管

默认 `--human-verification auto`。脚本用页面可见文字、标题与验证 iframe 识别真人验证，不将所有 403/405/429 都当成验证码。本机可操作桌面上，触发后打开一次可见浏览器，迁移本轮隔离上下文的内存状态；用户完成验证后继续使用同一个可见会话，保留响应监听与图片缓存。不会代做验证码，也不将验证 Cookie 写入报告或持久会话文件。

采集进程会把状态即时写入 `verification.json` 并输出 JSON 事件。Agent 一旦看到 `awaiting_human`，立即提示用户手动验证，并继续等待同一个进程；不要等进程结束才通知用户。使用宿主的异步执行/进程续查能力，避免短命令超时提前杀掉浏览器。默认等待 300 秒，可用 `--verification-timeout 600` 调整（30–900 秒）。完成后状态为 `completed`；取消、超时、再次被拦分别记录为 `cancelled`、`timed_out`、`blocked_again`，停止本轮后关闭浏览器。验证失败退出 3，不冒充零搜索结果。

容器、SSH、CI、xvfb、无显示服务或无法确认桌面的 Linux 环境，默认不弹不可操作的窗口，状态为 `requires_interactive_browser`。Agent 若有本机执行工具或平台远程浏览器接管能力，改在用户能操作的环境运行；否则说明当前沙箱无法展示窗口。仅运行 `xvfb-run ... --headed` 不提供用户接管入口。本机与云端的出口 IP、设备和会话不同，不能保证另一环境中验证过的 Cookie 有效。

平台已有用户可访问的远程桌面，且已确认与脚本是同一桌面时，可设置 `POD_HUABAN_INTERACTIVE=1` 启用接管；它不创建远程桌面，也不改变网站校验结果。`POD_HUABAN_INTERACTIVE=0` 表示当前桌面不可交互。明确只做无人值守诊断时用 `--human-verification off`，遇验证即返回接管需求；不要把这个选项加进普通试用提示词。

## 给试用者的提示词

将完整 skill 文件夹或试用包交给所用 Agent，安装时只需说：

```text
请安装我提供的 pod-image-selection skill。
```

正常使用（包括第一次查询）只需说：

```text
使用 $pod-image-selection，关键词：pet house。
```

环境校验、专用浏览器会话复用、竞品查询、花瓣阶段的匿名无头采集、看图去重和图集交付均由 skill 内部处理，不要求用户在提示词中复述。需要真人登录时由 Agent 在具体环节立即提示；不承诺永久有效。只安装时不主动查询素材；首次真实查询时再按需准备运行环境。

技能由 pod-huaban-materials 更名为 pod-image-selection；运行库缓存目录沿用旧名称，复用已安装的 Playwright，不要求重新下载。卖家精灵复用同一环境准备模块并单独检查原插件和专用配置，见 [卖家精灵浏览器授权](sellersprite-access.md)。
