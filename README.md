# amazon-skill

面向日本亚马逊运营的轻量技能库。

## POD 花瓣素材初筛

[pod-huaban-materials](skills/pod-huaban-materials/SKILL.md) 接收关键词与图片风格，搜索花瓣，结合右键找相似和推荐画板扩展，实际看图后去重，交付带图片、来源、筛选理由和授权状态的候选图集。默认 12 张，可补充产品、配色、比例、连续纹样和排除项。

```text
请使用 $pod-huaban-materials，找“猫”的素材，风格要复古木刻、黑白单色，适合 T 恤，初筛 12 张。
```

将整个 `skills/pod-huaban-materials/` 文件夹交给所用 Agent，说明“请安装这个 skill”即可。环境检查和依赖准备由 skill 在查询时自动处理，首次与后续使用的提示词相同。

优先使用独立 Playwright 无头脚本，批量捕获网站 JSON 和图片响应；本次未登录搜索 38 张约 3.3 秒，相似图和画板扩展约 8 秒缓存 60 个候选。无障碍定位可与无头模式同时使用，Browser Use 作为备用。首次使用自动校验 Node.js 18+、Python 3.9+、Playwright、浏览器启动和输出权限，按需准备私有运行库与 Chromium；也可用 `environment.cjs check/prepare/smoke` 分别检查本地环境、准备依赖及测试花瓣连接。详见[环境自检与试用提示词](skills/pod-huaban-materials/references/environment.md)和[访问说明](skills/pod-huaban-materials/references/huaban-access.md)；初筛素材的 POD 授权仍需核实。

## 日本站关键词查找

[amazon-jp-keywords](skills/amazon-jp-keywords/SKILL.md)（原 `amazon-jp-listing-asin`，中文名「日本亚马逊关键词调研」）支持两种找词方式，负责关键词与广告候选词采集。

- **根据 ASIN 找词**。用竞品 ASIN 或 `amazon.co.jp` 商品链接，通过老陈的易逊后端反查，并用 ABA 全站搜索词中命中竞品点击 ASIN 的记录补充候选。
- **以词找词**。用日文种子词或配件型号查 Amazon 日本站搜索框联想、厂家官网和 ABA 全站搜索词，三个渠道都尝试，再合并去重。查配件时核对在售、停售和停产机型。

官网原词和官网组合词在采集后按目标商品及购买意图洗词：泛功能描述、与整机目标不符的零部件词及缺少型号依据的词不直接进入主表，完整候选、结论和原因保存在官网明细。Amazon 搜索框联想（关联词）不洗词，易逊与 ABA 候选也按原有规则保留。多来源同词不会因官网筛选被误删，详见[官网词相关性筛选](skills/amazon-jp-keywords/references/keyword-cleaning.md)。

将整个 `skills/amazon-jp-keywords/` 文件夹放入所用 Agent 的技能目录。脚本在首次使用时按需准备依赖。易逊 `backend_token` 与 ABA MCP 客户端 `aba_mcp.access_token` 统一保存在 `~/.amazon-jp-listing-asin/config.json`；兼容其他 Agent 已写入的 `spapi_mcp.access_token`。用 `environment.py configure` 从标准输入合并更新，用 `environment.py config-status` 查看脱敏状态，`environment.py normalize-config` 可归一旧字段。

ABA 默认通过随附 `aba_client.py` CLI 直连服务，无需 Agent 注册 MCP，也无需安装 MCP SDK；只需 Python 3.9+。运行 `python3 skills/amazon-jp-keywords/scripts/aba_client.py status` 实测连接，`call` 执行工具，`collect-query` 完整采集并支持分页中断续取。选择原生 MCP 时也可通过 `aba_mcp_headers.py` 读取同一配置，详见[客户端配置](skills/amazon-jp-keywords/references/client-config.md)。

ABA 使用已连接的 `https://spapi-mcp.bamboost.info/mcp`，无需自有 ASIN；连接不可用或报表未完成时说明状态，继续交付已有候选。ABA 排名和份额分别标注，不当作月搜索量或易逊流量占比。报表与筛选任务保存 ID，可续查并复用压缩源缓存。

ABA 默认目标日期为查询日前 7 天，不含查询当天。例如 11 月 11 日查询对应 11 月 4–10 日，拆成 7 份 `DAY` 报表。`aba_client.py plan-window` 可生成逐日报表参数；默认查询日按北京时间，也可用 `--as-of YYYY-MM-DD` 指定。关键词合并去重，指标保留逐日明细。未取得的日期明确标注缺口，不用零补齐，不把每日指标相加或平均成 7 日指标。用户指定完整周、月或季度时仍支持相应单周期。

```text
请使用 $amazon-jp-keywords 反查我提供的日本亚马逊竞品 ASIN，整理前 20 个日文流量词和月搜索量。
请使用 $amazon-jp-keywords 以「水筒」和「ステンレスボトル」找词，合并 Amazon 日本站搜索框联想和厂家官网结果，官网词洗词、Amazon 联想词全部保留，交付主表和完整来源明细。
请使用 $amazon-jp-keywords 以 H060518 加湿器滤芯找词。查询 Amazon 日本站联想和大日官网，核对在售、停售及停产机型，用型号和官方部件名扩词，去重后附官方证据。
```

两种方式都交付 Excel。ASIN 反查交付 `keywords.xlsx`，以词找词交付 `seed-keywords.xlsx`；官网完整候选及洗词结论另存 `official-keywords.xlsx`，已取得的 ABA 商品与周期明细另存 `aba-keywords.xlsx`。报告说明官网洗前、保留、剔除及待核实数量。同一任务执行两种方式时，两份主表都交付。单独运行以下联想脚本可查看日文原词和联想顺序。

```sh
python3 skills/amazon-jp-keywords/scripts/amazon_jp_autocomplete.py \
  --output reports/jp-keywords/example/autocomplete.xlsx "水筒"
```

## 日本站 Listing 生成

[amazon-jp-listing](skills/amazon-jp-listing/SKILL.md) 接收关键词 XLSX/CSV/TSV、真实本品资料与图片，完整导入后进行购买意图筛词，生成日文标题、Highlight、五点、长描、后台搜索词、属性建议，以及主附图和 A+ 制作策划。支持单品和用户提供的父子体，交付 Markdown、JSON 和中文 HTML 验收报告。

流程：产品画像 → 完整导入词表 → 可用／暂缓／剔除三类词池 → 卖点与关键词布局 → 日文文案及图片策划 → 覆盖检查 → 本地／后端／独立语义验收。采集时保留的 Amazon 联想词在生成 Listing 时仍须按本品事实审核。

基于[老陈用户表格版](https://github.com/chenqinglin2019-tech/Laochen-Amazon-project/tree/87dead1472eb4ddfd28f99fd47a9593b5c3f7771/skills/lc_amazon_listing)的固定版本实现，只面向日本站。本地导入、筛词和渲染只需 Python 3.9+ 标准库；后端校验自动准备并核对 CLI，不需要注册 MCP，复用既有 `~/.amazon-jp-listing-asin/config.json` 的 `backend_token`。日本站明确跳过仅支持美国站的 QA 采集，仍整理买家问题覆盖。缺少后端或独立复审证据时，报告保留未完成状态。

```text
请使用 $amazon-jp-listing，根据我提供的 seed-keywords.xlsx 和商品图片、规格及包装清单，为日本站生成完整日文 Listing，完成意图筛词、主附图与 A+ 策划，并交付文案和验收报告。
```

关键词调研沿用历史配置与缓存路径，无需因技能改名重新配置 token。两个技能的职责、来源与执行命令分别见各自 `SKILL.md`。

## 日本站知识产权初筛

[amazon-jp-ipr-screening](skills/amazon-jp-ipr-screening/SKILL.md) 用于选品或上架前，检查单个商品在日本的特许、实用新案和意匠风险。默认中文交付，保留日语检索词和原始权利名称。

流程：商品资料 → J-PlatPat 接口检索 → 候选核对 → 风险与证据报告。

### 使用

把整个 `skills/amazon-jp-ipr-screening/` 文件夹放入所用代理的技能目录。

```text
请使用 $amazon-jp-ipr-screening，检查这个商品在日本亚马逊的特许、实用新案和意匠风险：

商品：提供 amazon.co.jp 链接，或商品图片、名称、用途及结构说明
销售方式：自有品牌 / 通用品牌 / 经销 / 兼容配件
已知线索：竞品、权利号、投诉记录或授权文件（有则提供）
```

可以先给商品链接和已有资料；缺失信息会按影响标注，不必先填完表格。只有竞品链接时，会先区分参考商品与准备销售的商品。

默认报告保存到 `reports/<日期>-<商品简称>/report.md`，证据放在同目录 `evidence/`。报告包含已查明风险、待定项、检索记录和下一步行动。
