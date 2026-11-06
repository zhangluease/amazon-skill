# amazon-skill

面向日本亚马逊运营的轻量技能库。

## 日本站知识产权初筛

[amazon-jp-ipr-screening](skills/amazon-jp-ipr-screening/SKILL.md) 用于选品或上架前，检查单个商品在日本的商标、意匠、特许、实用新案、著作权，以及有线索时的不正竞争风险。默认中文交付，保留日语检索词和原始权利名称。

流程：商品资料 → 日本公开检索 → 候选核对 → 风险与证据报告。

### 使用

把整个 `skills/amazon-jp-ipr-screening/` 文件夹放入所用代理的技能目录。Codex 用户可放入 `~/.codex/skills/`，再在能发现该技能的新会话中调用。技能本身不需要安装 Python 包、配置 API Key 或连接上游服务；实际检索需要宿主提供联网搜索或浏览器能力。

```text
请使用 $amazon-jp-ipr-screening，检查这个商品在日本亚马逊的知识产权风险：

商品：提供 amazon.co.jp 链接，或商品图片、名称、用途及结构说明
计划使用的品牌/Logo：填写实际使用内容；没有确定则说明未定
计划使用的图片/文案：说明哪些为自制、供应商提供或已获授权
销售方式：自有品牌 / 通用品牌 / 经销 / 兼容配件
已知线索：竞品、权利号、投诉记录或授权文件（有则提供）
```

可以先给商品链接和已有资料；缺失信息会按影响标注，不必先填完表格。只有竞品链接时，会先区分参考商品与准备销售的商品。

默认报告保存到 `reports/<日期>-<商品简称>/report.md`，证据放在同目录 `evidence/`。报告包含已查明风险、待定项、检索记录和下一步行动。本地报告默认不进入 Git。

### 这次精简

| 项目 | 日本版处理 |
| --- | --- |
| 国家 | 仅日本（JP）与 Amazon.co.jp |
| 检索 | J-PlatPat 为主要登记信息来源，补充日本官方资料与原始作品/品牌来源 |
| 日本用语 | 增加日语品名、读音、类似群代码及适用的意匠/技术分类 |
| 运行方式 | 代理按说明检索和核对，无上游云端鉴权、专有 CLI 或 API 依赖 |
| 交付 | 一份 Markdown 报告与必要证据；风险为高/中/低/待定 |
| 移除 | 多国分支、其他地区接口、付费额度调度、复杂流水线和固定双审/HTML 发布机制 |

登记信息无法读取、只做了搜索引擎发现、缺少必要视图或权利要求时，会保留对应缺口；检索无命中不等于无风险。这是一轮选品与上架前初筛，不构成法律意见或平台上架保证。

### 来源

参考 [Laochen-Amazon-project 的 lc-ipr-risk-screening-free](https://github.com/chenqinglin2019-tech/Laochen-Amazon-project/tree/87dead1472eb4ddfd28f99fd47a9593b5c3f7771/skills/lc-ipr-risk-screening-free) 的排查思路，基于提交 `87dead1472eb4ddfd28f99fd47a9593b5c3f7771`，于 2026-11-06 重新编写日本站流程。

原版在该快照的 `SKILL.md` 中说明：当前流水线只生成英文检索词，日本相关范围不检索。本版本补充可由代理实际执行的日语检索与证据核对流程，未复制上游脚本、凭据或服务配置，也不兼容其历史任务格式。

日本官方检索入口及依据见[日本检索指南](skills/amazon-jp-ipr-screening/references/japan-search.md)；报告格式见[报告模板](skills/amazon-jp-ipr-screening/references/report-template.md)。
