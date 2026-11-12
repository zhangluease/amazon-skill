---
name: amazon-jp-keywords
description: 查找日本亚马逊关键词。按竞品 ASIN 或 amazon.co.jp 商品链接反查流量词，或按日文种子词、配件型号汇总 Amazon 联想和厂家官网；使用日本站 ABA 全站搜索词补充候选及热度指标，无需自有 ASIN。官网词按目标商品相关性洗词，Amazon 联想不洗词；合并去重并交付关键词表与来源明细，配件查询含停售、停产机型。
---

# 日本亚马逊关键词调研

原名 `amazon-jp-listing-asin`。本技能承担关键词调研与广告候选词采集，Listing 文案生成由 `amazon-jp-listing` 承担。服务凭据统一读取项目公共配置。

本技能负责找词、官网词洗词、去重和记录来源。官网原词和官网组合词经目标商品相关性筛选后进入 Excel 主表，完整官网候选及筛选原因留在来源明细。Amazon 搜索框联想（关联词）不洗词，易逊与 ABA 候选也按原有规则保留。任务在交付关键词表后结束。标题、五点、后台搜索词、图片和 A+ 属于后续 Listing 工作。

## 输入

- **根据 ASIN 找词**。用户提供竞品 ASIN 或 `amazon.co.jp` 商品链接时，走 ASIN 反查。
- **以词找词**。输入日文种子词或配件型号时，执行该模式的全部渠道，再合并去重。同时提供竞品 ASIN 和种子词时，分别执行两种找词方式。
- 本品名称、用途和规格用于定位查询对象，并作为官网词洗词依据。先区分目标是整机、配件还是耗材，保留准确型号、版本和部件号。必保词和禁用词用于官网词筛选；来自 Amazon 联想、易逊或 ABA 的词仍保留并备注。已有有效 ASIN、种子词或可识别的配件型号即可先查。
- 两种方式均通过随附 CLI 尝试 ABA 全站搜索词补充，默认目标窗口为查询日前 7 天，不含查询当天，并拆为 7 份日报。流程见 [ABA 查询与字段口径](references/aba-search-terms.md)。无需自有 ASIN，也无需 Agent 注册 MCP。服务实际不可达或某日期未取得数据时，记录日期缺口并继续交付已取得的候选。
- 在 `reports/jp-keywords/` 下创建独立任务目录 `RUN`。下文命令中的 `SKILL` 指当前技能根目录，将路径替换为实际绝对路径。
- 使用当前 Agent 可调用的 Python 3.9+ 运行随附脚本。脚本按需准备运行环境，安装和复用成功时直接继续查询或导出。只在缺少 Python、依赖准备失败或需要用户补充凭据时说明具体问题。
- 两种找词方式都必须交付 Excel（`.xlsx`）。ASIN 反查交付 `keywords.xlsx`，以词找词交付 `seed-keywords.xlsx`。同一任务执行两种方式时，分别交付两份主表。

## 公共配置

易逊和 ABA MCP 共用 `~/.amazon-skill/config.json`：易逊使用 `backend_token`，ABA 客户端使用 `aba_mcp.url` 和 `aba_mcp.access_token`。ABA 字段保存的是我们 MCP 服务的 Bearer Token，不是亚马逊临时 access token。配置结构、写入和客户端读取方式见 [统一客户端配置](references/client-config.md)。

用 `python3 "SKILL/scripts/environment.py" config-status` 检查公共配置中的凭据状态，输出不含 token。配置或轮换时，通过 `environment.py configure` 的标准输入更新相应字段；脚本保留另一渠道与其他已有字段。在任一相关技能配置一次，同一用户安装的其他技能即可读取。只读取公共配置，不搜索技能目录或旧用户目录；ABA 字段统一为 `aba_mcp.access_token`。

### ABA CLI 调用入口

默认使用 [ABA CLI](scripts/aba_client.py) 从配置文件读取 token，直接通过 HTTP 调用我们的 MCP 服务。它与易逊的脚本调用方式一致，不依赖当前 Agent 是否列出了 MCP 工具。先执行真实连接检查：

```sh
python3 "SKILL/scripts/aba_client.py" status
```

检查成功后，运行 `aba_client.py plan-window --output "RUN/aba-window-plan.json"` 生成查询日前 7 天的逐日报表参数；默认查询日按北京时间，用户明确给出查询日时用 `--as-of YYYY-MM-DD`。例如 2026-11-11 查询，对应 2026-11-04 至 2026-11-10。计划只计算日期，不代表数据已经发布。

按计划逐日复用或创建报表，用 `aba_client.py call --tool 工具名 --arguments-file 参数JSON --output 结果JSON` 执行状态续查和 ASIN／种子词筛选，再用 `collect-query` 取完每个查询的分页，具体命令见 [ABA CLI 工作流](references/aba-search-terms.md#cli-调用方式)。保留每日指标和实际取得的日期范围。保存了 token 不等于完成连接验证；不能仅因 Agent 没有注册 MCP 或看不到原生工具，就认定 ABA 未接入或跳过查询。用户明确选择原生 MCP 工具时，可使用同名工具和 [请求头读取脚本](scripts/aba_mcp_headers.py)。

## ASIN 反查

从 `/dp/<ASIN>` 或 `/gp/product/<ASIN>` 商品链接提取 ASIN。去除空白、转为大写，校验为 10 位字母或数字，按输入顺序去重。列出被跳过的无效输入和非日本站链接。单次查询最多 20 个去重后的 ASIN，超出时请用户选定本轮 ASIN。

1. 用随附的 [ASIN 反查脚本](scripts/environment.py) 向老陈的易逊后端整组反查。脚本按平台自动准备并校验固定版本的 CLI 和调用脚本，缓存到本机用户目录。ASIN 用英文逗号连接。

   ```sh
   python3 "SKILL/scripts/environment.py" expand \
     --asins "ASIN1,ASIN2" --output "RUN/02_kw_raw.json"
   ```

2. 优先复用本技能或本机已配置的易逊凭据。命令提示缺少凭据时，只请用户配置易逊 token。通过 `python3 "SKILL/scripts/environment.py" configure` 的标准输入传递含 `backend_token` 的 JSON，保存到用户目录 `~/.amazon-skill/config.json`。凭据只通过安全的标准输入或本机配置传递，避免出现在命令参数、日志和报告中。
3. 默认复用同一报告根目录下、相同 ASIN 集合且同为日本站的 7 天内成功结果。写入和读取缓存时都检查业务状态和响应结构。失败、未完成或损坏的结果仅供排查。用户要求刷新时用 `--reuse-days 0`。记录站点、请求 ASIN、实际查询或缓存来源，以及响应明确提供的数据日期。查询日期与数据日期分开记录。
4. 同时检查命令摘要和 `02_kw_raw.json`。缓存命中时仍检查业务状态和错误字段。区分环境准备、鉴权、额度、网络、部分结果和后端明确返回的无数据。响应缺失、结构异常或失败时，按实际状态交付已有有效词，避免把失败当作零匹配。超时或提交状态未知时，先核对请求状态。
5. 按 [ABA 全站搜索词补充](references/aba-search-terms.md) 查询相同的竞品 ASIN，筛选 `clickedAsin` 命中的词，保留来源、统计周期和商品明细。易逊无数据或失败时仍执行 ABA。两渠道候选合并进 `keywords.xlsx`；ABA-only 候选的月搜索量和易逊流量占比留空，ABA 指标单列。ABA 未命中不代表竞品没有该词流量。

## 以词找词

每轮都尝试 Amazon 搜索框联想、厂家官网和 ABA 全站搜索词三个渠道。一个渠道已找到候选、无结果或查询失败时，仍执行其余渠道。收集完成后汇总候选并保留来源，再执行官网词洗词和去重。

### Amazon 搜索框联想

使用随附的 [Python 脚本](scripts/amazon_jp_autocomplete.py) 直连 `completion.amazon.co.jp`。查询无需浏览器、Amazon 登录或易逊 token。每个种子词或部件号作为一次搜索前缀请求，保留接口返回顺序。

```sh
python3 "SKILL/scripts/amazon_jp_autocomplete.py" \
  --output "RUN/autocomplete.xlsx" "水筒" "ステンレスボトル"
```

脚本先将候选和逐种子词查询状态写入 `autocomplete.json`，再导出 Excel。终端 JSON 摘要分别记录查询和导出状态。查询成功但无候选会标为 `no_matches`，遇到 HTTP 403 或 429 时，剩余请求标为 `skipped`。导出失败时用 `export_xlsx.py --input "RUN/autocomplete.json" --output "RUN/autocomplete.xlsx"` 从已保存的数据重试。Excel 每行记录来源、种子词、联想位置和日文原词。同一候选来自多个种子词时，保留各自的位置。联想来源的月搜索量和流量占比留空。

### 厂家官网

- **品名或品类词**。优先查用户提供的产品官网；品牌未知时，用种子词定位日本市场同品类的厂家产品页、规格表或说明书，提取与目标商品有关的日文品名、通用规格和用途词。逐词保留官方出处。
- **配件或部件号**。按 [官网适配型号和部件名组合查询](references/official-compatibility.md) 核对在售产品、独立的停售或停产入口和旧款资料。提取官方日文部件名和已确认的适配整机型号，再以部件号或适配整机型号为前缀，与部件名组合扩词。联想中出现的机型先按原词收录，核实后才标为已确认适配。记录实际覆盖范围和缺口。

### 官网词洗词

采集后、主表交付前，按 [官网词相关性筛选](references/keyword-cleaning.md) 逐词检查「官网原词」和「官网组合」。先核对目标商品，再判断该词是否表达对应的商品购买意图。官网出现过、来自同品牌页面或已经加上型号，都不能单独作为保留依据。

- 过滤脱离商品主体的泛功能词、营销或操作描述、整机任务中的零部件词，以及缺少目标型号或适配依据的其他机型词。不得把相近型号页面的功能当作目标 ASIN 的规格，也不得仅靠添加品牌或型号绕过筛选。
- Amazon 联想不参与洗词；原词、变体及版本差异按原有规则保留。同词同时来自官网与 Amazon 联想时，官网筛选结论不能删除这条联想词。易逊和 ABA 来源同样不受本次官网词洗词影响。
- 官网候选标为「保留」「剔除」「待核实」或「用户指定保留」，记录原因及目标关联证据。仅官网来源且被剔除或待核实的词不进入主表，完整记录留在 `official-keywords.xlsx`。信息不足时交付已取得的其他来源，不把官网待核实项默认算作相关词。

### 合并与标注

先按 [ABA 全站搜索词补充](references/aba-search-terms.md) 使用日文种子词、部件号和已确认的官方部件名或型号写法筛选。记录实际筛选词和覆盖范围，匹配不能作为已确认适配的依据。

1. 汇总三个渠道的候选。去重时去除首尾空白，并仅对已识别的完整部件号和整机型号统一字母大小写及全半角，例如 `ｈ０６０５１８` 和 `h060518` 与 `H060518` 归为同一型号。保留连字符位置和型号后缀。处理后完全相同的关键词合并为一行，型号优先采用官网写法。来源明细保留各渠道的原文、种子词和联想位置，以及官网 URL 和证据位置。区分 Amazon 联想、官网原词、官网组合和 ABA 全站搜索词。其他写法差异可归入同一搜索意图组，逐词保留。
2. 按全部来源决定主表去留：具有 Amazon 联想或 ABA 来源的候选全部保留；仅官网来源的候选按官网词洗词结论处理。不能因单渠道命中或 ABA 未命中而剔除官网相关词。查配件时按[候选词的版本和属性标注](references/official-compatibility.md#候选词的版本和属性标注)记录已有的证据，区分「联想保留，备注版本差异」与「官网待核实，仅留明细」。
3. 交付去重后的关键词主表 `seed-keywords.xlsx`，包含日文关键词、意图或词类、全部来源及其证据、事实备注、保留依据。保留 `autocomplete.xlsx` 作为完整联想明细，`official-keywords.xlsx` 作为完整官网原词、组合词及洗词结论明细，`official-models.xlsx` 作为适配型号明细。ABA 明细交付 `aba-keywords.xlsx`，主表保留可追溯的 ABA 指标或明细位置；同词对应多个部门或 ASIN 时分别保留。月搜索量和易逊流量占比留空。

## 整理与交付

- ASIN 反查的关键词取自响应的 `keywords` 数组（字符串或含 `keyword` 的对象），缺少该数组时读取 `raw.keyword_data`。按原词关联 `raw.keyword_data` 里的指标。月搜索量使用 `searches` 或 `monthly_searches`，流量占比使用 `trafficPercentage` 或 `traffic_percentage`。指标缺失标记「未返回」，保留真实的零。逐词命中 ASIN 和排名仅在响应有明确对应关系时列出，整组查询的输入 ASIN 另列。
- ASIN 反查保留服务端返回的日文原词，仅合并去除首尾空白后完全相同的词。平假名、片假名、汉字、空格差异和长尾变体可归到同一意图组，仍逐词保留指标。重复记录的流量占比不相加。指标冲突时标注各自来源或口径。
- ABA 的搜索频率排名、搜索部门、点击 ASIN、点击份额排名、点击份额、转化份额和统计日期分别记录，不填入易逊月搜索量或流量占比列。份额为 0–1 的比例，保留零和缺失的区别；转化份额不是转化率，点击份额排名不是自然排名。跨部门、跨周期和跨 ASIN 的指标不混合。滚动 7 天只合并候选词，逐日排名与份额不相加，也不简单平均为 7 日指标；报告列出请求日期、已取得日期和缺口日期。联想、官网和易逊候选不因 ABA 未命中而删除。详见 [ABA 指标与合并边界](references/aba-search-terms.md#指标解释与合并边界)。
- 可按品名、属性、用途、人群或场景做意图分组，并标注竞品品牌、版本差异、待核实的关系或用户提供的禁用词。Amazon 联想、易逊和 ABA 候选保留原词与来源；官网候选按洗词结论分流到主表或官网明细。有必保词未命中时，单独标记为用户提供、查询未命中。
- 按 [关键词报告模板](references/report-template.md) 在任务目录生成中文 `report.md`。报告中每种找词方式展示最多 20 个关键词或用户指定的数量，Excel 主表保留全部符合来源与洗词规则的词，不受摘要条数限制。交代各渠道查询状态和官网洗词数量，在末尾链接本轮工作簿和可用证据，本地文件用相对路径。已执行的方式没有采集到关键词，或官网候选全部未通过且没有其他来源候选时，仍生成带表头的空结果主表，并在报告区分未采集到与洗词后无词。导出失败时，保留已取得的数据并报告导出状态。

### 导出整理后的关键词表

将整理后的表写成临时 JSON，包含 `headers`（表头字符串数组）、`rows`（按表头顺序排列的二维数组），以及可选的 `percentage_columns`（需要显示为百分比的表头名称数组）。主表与完整官网明细分别准备 JSON；官网明细包含洗词结论、原因和目标关联证据，不得在导出时再次截断或过滤原始记录。月搜索量用数字，缺失值可留空。已确认按比例返回的流量占比用数值和 Excel 百分比格式，例如 `0.0155` 显示为 `1.55%`。口径未确认时保留原值，商品型号和 ASIN 保留为文本。

```sh
python3 "SKILL/scripts/export_xlsx.py" --input "关键词表临时JSON绝对路径" \
  --output "RUN/keywords.xlsx"
```

导出以词找词的候选表或官网明细时，将输出文件名改为相应的工作簿名称。

ASIN 查询参考 [lc-amazon-listing-asin 的整组扩词步骤](https://github.com/chenqinglin2019-tech/Laochen-Amazon-project/blob/main/skills/lc-amazon-listing-asin/INSTRUCTIONS.md#2-整组扩词1-次请求)。
