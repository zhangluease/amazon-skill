# 项目公共客户端配置

本项目的相关技能使用一份公共配置：`~/.amazon-skill/config.json`。这台机器上的 `~` 是当前运行 Agent 的用户目录，与技能源码、安装位置和报告目录分开。同一用户在 Trae、Codex 或不同项目中分别安装关键词与 Listing 技能，也读取相同的易逊凭据。

```json
{
  "backend_url": "https://mcp.yixunkuajing.com",
  "backend_token": "",
  "aba_mcp": {
    "url": "https://spapi-mcp.bamboost.info/mcp",
    "access_token": ""
  }
}
```

`aba_mcp.access_token` 是连接我们部署的 MCP 服务的客户端凭据，不是亚马逊 LWA 临时 access token。亚马逊 refresh token、client secret 和临时 access token 仍由服务器管理。

ABA 客户端字段固定为 `aba_mcp.access_token`，填写不含 `Bearer ` 前缀的 token。只使用这份新公共配置，不导入旧技能目录中的文件或历史字段。

## 写入与检查

仓库安装可用 `python3 scripts/configure.py init` 创建空公共配置，用 `python3 scripts/configure.py configure` 从标准输入更新。独立安装本技能时，通过 `python3 "SKILL/scripts/environment.py" configure` 的标准输入传入 JSON，效果相同。支持只更新一个渠道，也支持同时更新；未提供的配置字段保持原值。不要把真实 token 写进命令参数、技能源码、工具参数或报告。

只更新 ABA 的输入格式：

```json
{"aba_mcp":{"access_token":"<新的 MCP 客户端 token>"}}
```

只更新易逊时，传 `{"backend_token":"<新的易逊 token>"}`。写入 ABA 时，URL 默认填入上述固定地址。更新一个渠道不会清空另一个渠道的凭据；显式传空字符串可清除该渠道的 token。更换 `backend_url` 时须同时提供新地址对应的 `backend_token`，避免跨服务复用凭据。

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

将示例路径替换为当前安装路径。客户端凭据统一放在公共 `~/.amazon-skill/config.json` 中，连接配置通过 helper 读取。随包的 `amazon_config.py` 是公共读取器，`skill_config.py` 仅提供本技能入口；完整安装技能文件夹即可，不依赖另一个技能或仓库根目录。

不支持 header helper 的客户端需在连接层从该文件读取 `aba_mcp.access_token` 并构造 Bearer 请求头。只有运行时进程读取 token，模型和工具业务参数无需接收它。

轮换 token 后通过 `aba_status` 实测连接；本地显示已配置仅表示有符合格式的凭据。连接层可能缓存请求头，轮换后按客户端机制刷新。公共读取器的维护源为项目 `shared/amazon_config.py`，发布时用 `scripts/sync_shared_config.py` 同步随包副本。
