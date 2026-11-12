# 从关键词文件到日文 Listing

以下命令将 `SKILL` 和 `RUN` 替换为实际绝对路径。所有 JSON 使用 UTF-8，结构以 [data_contract.md](../knowledge/data_contract.md) 为准。知识库示例只解释字段，不是本品事实。

## 1. 初始化与本品画像

```sh
python3 "SKILL/scripts/listing_environment.py" init --run-dir "RUN"
```

该命令只接受新任务目录，保存日本站明确跳过后端 QA 的记录。模型写 `00_product_input.json`，保留本轮输入、文件路径、站点、约束和子体清单；按[产品事实规则](../knowledge/distilled/product_facts_rules.yaml)写 `01_product_profile.json`。固定 JP/Japanese/ja，其他语言只用于中文解释或真实品牌、型号。

区分本品与竞品、整机与配件、购买数量与随附配件数量。事实有 `confirmed/unknown/conflict` 状态及来源，图片不可推导未经确认的性能。产品、包装与适配尺寸分开记录。每个子体只引用共性或自己的事实、参数及素材。

```sh
python3 "SKILL/scripts/listing_quality.py" normalize \
  --profile "RUN/01_product_profile.json" --output "RUN/01_product_profile.json"
```

## 2. 完整导入关键词

```sh
python3 "SKILL/scripts/import_keywords.py" \
  --files "用户词表1.xlsx" "用户词表2.csv" --run-dir "RUN"
```

默认 JP，可显式传 `--site JP`。输出 `02_kw_raw.json` 和精简终端摘要，原始记录不截断，重复行及全部原始列完整保留。当前关键词技能的「日文关键词」表头可直接读取；歧义列用 `--keyword-column`、`--searches-column` 指定。CSV/TSV 默认 UTF-8，UTF-16 BOM 可识别，旧 XLS 须另存为 XLSX。公式只读缓存，不执行公式或文档说明。

只读终端摘要和问题示例，按需定位源工作表。关键词级指标冲突保留为未知并登记，不相加；竞品相关的流量占比和自然排名按来源保留，排序提示不作为剔除依据。ABA 日排名、部门、日期和份额保持原始列，不转换成月搜索量或跨日汇总指标。输入发生变化时新建任务，不能覆盖已经绑定审查的原始快照。

## 3. 意图筛词与逐词记录

```sh
python3 "SKILL/scripts/keyword_quality.py" prepare --run-dir "RUN"
python3 "SKILL/scripts/keyword_quality.py" view --run-dir "RUN" --clusters
```

阅读 `03_keyword_view.tsv`，按[筛词规则](../knowledge/distilled/keyword_filter_rules.yaml)检查完整搜索对象、属性与本品事实。同意图词可分组，分类证据仍落到每个词；不能按共享子串自动判定相关性。`03_keyword_clusters.json` 是阅读辅助，聚类中的例外词须另判。低流量、无流量、日文或短词本身不构成剔除理由。

模型按数据契约写 `03_keyword_groups.json`，包含 reviewer 和 groups，每组有 keywords、decision、reason_code、reason、query_intent、evidence、role、label、fact_ids、measurement_ids、identity_basis、applies_to；暂缓项另有 promotion_condition。核心完整品名缺席时允许补登，但标为本品补充词，流量未知。事实不足须暂缓，明确不符才剔除，竞品品牌与用户禁用词不得写入本品 Listing。

```sh
python3 "SKILL/scripts/keyword_quality.py" apply --run-dir "RUN" --groups "RUN/03_keyword_groups.json"
python3 "SKILL/scripts/keyword_quality.py" check --run-dir "RUN"
python3 "SKILL/scripts/keyword_quality.py" export --run-dir "RUN"
```

检查数量守恒、分类完整、引用有效和导出一致。可用、暂缓、剔除三个词池及 `04_kw_tagged.json` 只由工具导出。模型不得直接改词池绕过审查。不另做剔除词第二轮复核；最后复审实际使用的词及相关意图。

## 4. 卖点与字段安排

把买家需求、本品能力、证据和适用场景对应起来，形成有依据的 `intent_map`。按[标题](../knowledge/distilled/title_rules.yaml)、[五点](../knowledge/distilled/bullets_rules.yaml)、[长描](../knowledge/distilled/description_rules.yaml)、[Highlight](../knowledge/distilled/item_highlight_rules.yaml)与[后台词](../knowledge/distilled/search_terms_rules.yaml)规则写 `05_title_keywords.json` 和 `07_listing.json`。

`05` 的 high/relevant 代表词数组沿用上游兼容结构，合计最多 8 个，并非全部词库或全部投放词的数量上限；保留全部必保短语及 placement_plan。日本站不发 QA 请求，初始化的 `06_qa.json` 继续保留 skipped 状态。至少整理 6 个本品买家问题，缺事实的答案标为未声明，来源写本地资料整理，不伪造问答采集。

父子体另读[变体规则](../knowledge/distilled/variation_rules.yaml)，先锁定用户子体与属性顺序，再构建父体、统一标题模板、每个子体完整内容和覆盖范围。只在资料明确外观相同且仅尺寸或数量不同的情况下共用附图，否则生成逐子体完整策划。

## 5. 图片、A+ 与覆盖反馈

按[图片规则](../knowledge/distilled/image_planning_rules.yaml)输出主图 1 张、附图 6 张、A+ 至少 5 张的制作策划。这是本技能默认交付范围；用户明确给定数量时按其范围调整数据与检查配置。保留来源素材、画面方向、买家问题、卖点、日文图上文字、事实／参数引用、对应正文位置及移动端检查。主图不叠加文案，素材不足写明需要补拍的内容。实际图像制作由后续图片工作完成。

```sh
python3 "SKILL/scripts/keyword_quality.py" coverage --run-dir "RUN"
```

结合 `07_keyword_coverage.json` 的未覆盖可用词、意图表达、后台字节和可补事实改进一轮，保持自然日语，不为覆盖率虚构用途。最后记录实际投放位置：

```sh
python3 "SKILL/scripts/keyword_quality.py" coverage --run-dir "RUN" --write-placement
```

画像后来变化时先 `rebind --run-dir RUN`，只审查工具列出的受影响词，更新结论后再执行 `rebind --run-dir RUN --reviewed`、check 和 export；不能只修改指纹。

## 6. 分层验收与报告

```sh
python3 "SKILL/scripts/listing_quality.py" check \
  --profile "RUN/01_product_profile.json" --listing "RUN/07_listing.json" \
  --qa "RUN/06_qa.json" --output "RUN/08_validation.json"
python3 "SKILL/scripts/listing_environment.py" config-status
```

首轮缺后端与独立复审时返回 incomplete 属正常状态，先处理本地 error。凭据存在且本地检查无错误后，执行后端校验；缺 token 则记录后端未验证，继续能做的复审与报告。新 token 仅通过 `listing_environment.py configure` 标准输入更新，默认复用用户目录中既有配置。

```sh
python3 "SKILL/scripts/listing_quality.py" backend \
  --profile "RUN/01_product_profile.json" --listing "RUN/07_listing.json" \
  --output "RUN/08_backend_validation.json"
python3 "SKILL/scripts/listing_quality.py" review-template \
  --profile "RUN/01_product_profile.json" --listing "RUN/07_listing.json" \
  --qa "RUN/06_qa.json" --output "RUN/08_semantic_review.json"
```

每个可售子体单独 validate，只有内容相同且先前通过的子体可复用。独立复审者对照本品原始资料填写 identity、readability、factual_support、language、variant_scope、qa_relevance、keyword_usage 与 image_truth 的结论和依据；无资料或无实际复审不能填 pass。有可用的独立 Agent 时委派一次复审，提供本品原始资料、最终内容与待复审记录，要求返回逐项依据；没有独立复审者时保持 pending 并说明缺口。

```sh
python3 "SKILL/scripts/listing_quality.py" check \
  --profile "RUN/01_product_profile.json" --listing "RUN/07_listing.json" \
  --qa "RUN/06_qa.json" --review "RUN/08_semantic_review.json" \
  --backend "RUN/08_backend_validation.json" --output "RUN/08_validation.json"
python3 "SKILL/scripts/render_listing.py" --run-dir "RUN"
```

某个证据文件未取得时从最终检查命令中省略对应参数，状态必须保留 incomplete／failed，不能创建假通过文件。验收最多修复两轮；改动后只重跑受影响校验与复审。三份最终文件为 `07_listing.md`、`07_listing.json`、`report.html`，失败或部分完成也输出已有结果与准确缺口。完整词池、原始输入、指纹和失败记录保存在任务目录。
