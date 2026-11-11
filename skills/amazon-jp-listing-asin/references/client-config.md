# 统一客户端配置

本技能只使用一份客户端配置：`~/.amazon-jp-listing-asin/config.json`。这台机器上的 `~` 是当前运行 Agent 的用户目录，与技能源码和报告目录分开。现有易逊凭据与 ABA MCP 客户端凭据合并保存在同一文件中。

```json
{
  "backend_url": "https://mcp.yixunkuajing.com",
  "backend_token": "<易逊 token；未配置时为空字符串>",
  "aba_mcp": {
    "url": "https://spapi-mcp.bamboost.info/mcp",
    "access_token": "<ABA MCP 客户端 Bearer Token，不含 Bearer 前缀>"
  }
}
```

`aba_mcp.access_token` 是连接我们部署的 MCP 服务的客户端凭据，不是亚马逊 LWA 临时 access token。亚马逊 refresh token、client secret 和临时 access token 仍由服务器管理。

兼容历史或其他 Agent 写入的 `spapi_mcp.access_token`，未写 `url` 时使用固定服务地址。CLI 和请求头 helper 均可直接读取该字段，无需重新提供 token。运行 `environment.py normalize-config` 可将旧节合并到 `aba_mcp` 并移除旧节，保留易逊和其他字段。两节存在不同 token 时明确返回 `ABA_MCP_CONFIG_CONFLICT`，不猜测应使用哪个凭据。

## 写入与检查

通过 `python3 "SKILL/scripts/environment.py" configure` 的标准输入传入 JSON。支持只更新一个渠道，也支持同时更新；未提供的配置字段保持原值。不要把真实 token 写进命令参数、技能源码、工具参数或报告。

只更新 ABA 的输入格式：

```json
{"aba_mcp":{"access_token":"<新的 MCP 客户端 token>"}}
```

只更新易逊时，沿用 `{"backend_token":"<新的易逊 token>"}`。写入 ABA 时，URL 默认填入上述固定地址。创建主配置前，脚本会尝试合并原易逊技能中已配置的后端凭据，避免新建 ABA 配置后覆盖旧的易逊读取路径；已明确设置其他 backend URL 时不会把不同后端的 token 混入。

配置文件以原子替换写入。在 POSIX 系统中，目录权限为 `0700`、文件权限为 `0600`。凭据配置不依赖下载第三方脚本。使用下面的命令查看路径和凭据是否已配置，输出不包含 token 值：

```sh
python3 "SKILL/scripts/environment.py" config-status
```

空的 `backend_token` 表示易逊尚未配置，不影响已配置的 ABA 渠道。

## MCP 客户端读取

默认使用随附 CLI，直接读取这份配置并访问 HTTP MCP 服务，不需要 Agent 的 MCP 注册配置：

```sh
python3 "SKILL/scripts/aba_client.py" status
python3 "SKILL/scripts/aba_client.py" tools
```

状态检查是实际网络请求；`environment.py config-status` 只检查本地配置。之后通过 CLI 的 `call` 和 `collect-query` 执行查询与完整分页采集，见 [CLI 工作流](aba-search-terms.md#cli-调用方式)。配置保存成功后应实际执行状态检查，不能直接标为“脚本未接入，不会用于查询”。

### 可选的原生 MCP 接入

随附的 `scripts/aba_mcp_headers.py` 从同一 `config.json` 读取 ABA token，向 MCP 客户端输出 `Authorization` 请求头。它的标准输出是认证协议数据，只供客户端读取；不要直接把脚本输出打印进聊天、终端摘要或日志。

对于支持 `http_headers_helper` 的本地 HTTP MCP 客户端，连接配置只记录地址和读取脚本的绝对路径。以 Codex 为例：

```toml
[mcp_servers.amazon_sp_api]
url = "https://spapi-mcp.bamboost.info/mcp"
http_headers_helper = "python3 '/技能实际绝对路径/scripts/aba_mcp_headers.py'"
```

将示例路径替换为当前安装路径。客户端凭据统一放在本技能的 `config.json` 中，连接配置通过 helper 读取；不要再同时配置其他 Authorization 或 Bearer 来源。已部署的本地 helper 也可以使用同目录的 `skill_config.py` 模块，读取位置一致。

不支持 header helper 的客户端需在连接层从该文件读取 `aba_mcp.access_token` 并构造 Bearer 请求头。只有运行时进程读取 token，模型和工具业务参数无需接收它。

迁移已有凭据时先保存统一配置、更新读取脚本，再用新脚本取得的请求头验证 `aba_status`。验证成功后移除旧的单独 token 文件，保留一份客户端凭据配置。连接层可能缓存请求头，轮换后按客户端机制刷新；Codex 的 helper 行为见 [官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。
