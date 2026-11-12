# 环境自检与首次试用

Node.js 18+ 与 Python 3.9+ 是基础运行条件。Playwright 固定为 skill 的 package.json 中版本；浏览器优先用本机 Chrome，也支持 Playwright 管理的 Chromium。小伙伴只需安装完整 skill 文件夹并提供关键词、风格、数量，Agent 按本说明处理环境；首次与后续使用的提示词相同，无需描述环境准备步骤。

## 入口与结果

在 skill 目录运行下列命令，或把 scripts 路径改成实际绝对路径。`--out` 指向用户当前项目中可写的目录，不依赖原开发者的用户名、工作目录或父目录 npm 包。

| 命令 | 作用 | 是否访问花瓣 |
| --- | --- | --- |
| `node scripts/environment.cjs check --out /absolute/output/environment` | 检查 Node、Python、npm、Playwright 版本、输出写入，并实际启动浏览器 | 否 |
| `node scripts/environment.cjs prepare --out /absolute/output/environment` | 同上；缺依赖时按需准备，再检查 | 否；必要时访问 npm 和官方浏览器下载源 |
| `node scripts/environment.cjs smoke --query '狗 动漫' --limit 3 --out /absolute/output/smoke` | 小样本验证匿名搜索、数据读取与本地预览缓存 | 是 |

`check` / `prepare` 成功退出 0，环境未就绪退出 2。`smoke` 环境失败退出 2，网站或采集链路失败退出 3。输出 JSON 的 `checks` 指明失败项与 remedy；不要依赖终端最后一行“命令执行完成”判断成功。

`environment.json` 的 `ready=true` 只说明本地可运行；不代表花瓣网络或账号权限可用。`smoke.json` 另记真实联网结果。普通采集命令内置同样的检查和按需准备，并复用检查时启动的浏览器，不额外启动第二个浏览器来采集。

## 依赖如何准备

先寻找 skill 可用的锁定版本 Playwright，再寻找本技能私有缓存。已有其他版本时，不升级或卸载全局包，按需另装锁定版本。私有运行库默认位置：

- macOS：`~/Library/Caches/pod-huaban-materials/playwright-<版本>/`
- Linux：`$XDG_CACHE_HOME/pod-huaban-materials/playwright-<版本>/`，未设置时为 `~/.cache/...`
- Windows：`%LOCALAPPDATA%\pod-huaban-materials\playwright-<版本>\`

可通过 `POD_HUABAN_CACHE_DIR` 指定缓存根目录；通过 `POD_HUABAN_PYTHON` 指定 Python 可执行文件路径。解释器自动尝试 python3、python、py -3，选择满足版本的一个，报告完整可用调用方式。

优先实际启动 Chrome；没有可用 Chrome 时尝试已安装的匹配版 Chromium，必要时调用锁定版 Playwright CLI 准备 Chromium。浏览器二进制使用 Playwright 官方缓存机制。可用 `--browser chromium` 验证管理版浏览器，或 `--browser chrome` 明确只选 Chrome。采集命令也支持此选项。

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

首次试用先尝试匿名公开内容。本次匿名模式已实测可用，但不保证所有网络与时间；出现明确登录要求时才交由用户在可见浏览器登录。不要要求用户在聊天里粘贴 Cookie 或密码。

## 给试用者的提示词

将完整 skill 文件夹或试用包交给所用 Agent，安装时只需说：

```text
请安装我提供的 pod-huaban-materials skill。
```

正常使用（包括第一次查询）只需说：

```text
使用 $pod-huaban-materials，找 10 张动漫风狗狗图片，用作 T 恤图案灵感。
```

自检、按需准备依赖、匿名无头采集、看图去重和图集交付均由 skill 内部处理，不要求用户在提示词中复述。只安装时不主动查询素材；首次真实查询时再按需准备运行环境。
