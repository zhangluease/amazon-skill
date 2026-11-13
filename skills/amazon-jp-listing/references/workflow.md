# 从关键词文件到日文 Listing

以下命令将 `SKILL` 和 `RUN` 替换为实际绝对路径。所有 JSON 使用 UTF-8，结构以 [data_contract.md](../knowledge/data_contract.md) 为准。用户附件可任意命名，按格式、表头与内容识别；命令中的文件路径是占位符，RUN 内编号文件名是脚本接口。知识库示例只解释字段，不是本品事实。

## 1. 初始化与本品画像

```sh
python3 "SKILL/scripts/listing_environment.py" init --run-dir "RUN"
```

该命令只接受新任务目录，保存日本站明确跳过后端 QA 的记录。模型写 `00_product_input.json`，保留本轮输入、文件路径、站点和约束；按[产品事实规则](../knowledge/distilled/product_facts_rules.yaml)写 `01_product_profile.json`。固定 JP/Japanese/ja，其他语言只用于中文解释或真实品牌、型号。

区分本品与竞品、整机与配件、购买数量与随附配件数量。事实有 `confirmed/unknown/conflict` 状态及来源，图片不可推导未经确认的性能。产品、包装与适配尺寸分开记录。先读[耗材选购依据与编号角色](consumables.md)，记录 compatibility_basis 和 compatibility_reason；按对象型号选购且有适配清单时读[清单规则](compatibility-lists.md)，将原件中的每个完整型号录入 compatible_models 事实并保留来源／状态，给定词中语义明确的适配对象直接采用，不做二次核验。只使用本品的事实、参数及素材。

```sh
python3 "SKILL/scripts/listing_quality.py" normalize \
  --profile "RUN/01_product_profile.json" --output "RUN/01_product_profile.json"
```

## 2. 完整导入关键词

```sh
python3 "SKILL/scripts/import_keywords.py" \
  --files "用户词表1.xlsx" "用户词表2.csv" --run-dir "RUN"
```

默认 JP，可显式传 `--site JP`。输出 `02_kw_raw.json` 和精简终端摘要，原始记录不截断，重复行及全部原始列完整保留。中／英／日文常见关键词表头可直接读取；歧义列用 `--keyword-column`、`--searches-column` 指定。CSV/TSV 默认 UTF-8，UTF-16 BOM 可识别，旧 XLS 须另存为 XLSX。公式只读缓存，不执行公式或文档说明。

只读终端摘要和问题示例，按需定位源工作表。关键词级指标冲突保留为未知并登记，不相加；竞品相关的流量占比和自然排名按来源保留，排序提示不作为剔除依据。ABA 日排名、部门、日期和份额保持原始列，不转换成月搜索量或跨日汇总指标。输入发生变化时新建任务，不能覆盖已经绑定审查的原始快照。

## 3. 可信词分组与逐词记录

```sh
python3 "SKILL/scripts/keyword_quality.py" prepare --run-dir "RUN"
python3 "SKILL/scripts/keyword_quality.py" view --run-dir "RUN" --clusters
```

阅读 `03_keyword_view.tsv`，按[分组规则](../knowledge/distilled/keyword_filter_rules.yaml)理解完整含义和安排角色，不再核验给定关键词的真伪或要求独立外部证明。prepare 生成 `02_identifier_roles.json` 中立候选；根据本品身份、字段标签和完整关键词填写 identifier／role／source_positions／reason，可补带空格或纯名称标识。角色见[耗材规则](consumables.md)，不是外部核验。完成后执行下列 register-models，同步 compatible_models 事实和对应原词；有尚未解释的角色时工具报未完成，不按编号前缀猜测。

```sh
python3 "SKILL/scripts/keyword_quality.py" register-models --run-dir "RUN"
```
同意图词可分组，分类证据仍落到每个词；不能按共享子串自动判定相关性。`03_keyword_clusters.json` 是阅读辅助，聚类中的例外词须另判。低流量、无流量、日文或短词本身不构成剔除理由。

模型按数据契约写 `03_keyword_groups.json`，包含 reviewer 和 groups，每组有 keywords、decision、reason_code、reason、query_intent、evidence、role、label、fact_ids、measurement_ids、identity_basis、applies_to；暂缓项另有 promotion_condition。核心完整品名缺席时允许补登，但标为本品补充词，流量未知。给定词默认可用；仅无法解析、明确否定或资料具体冲突时记录暂缓／剔除及原文依据，用户明确禁用词继续遵守；给定兼容品牌只按兼容语义使用，后台字段的品牌限制单独处理，不删整行适配型号。

```sh
python3 "SKILL/scripts/keyword_quality.py" apply --run-dir "RUN" --groups "RUN/03_keyword_groups.json"
python3 "SKILL/scripts/keyword_quality.py" check --run-dir "RUN"
python3 "SKILL/scripts/keyword_quality.py" export --run-dir "RUN"
```

检查数量守恒、分类完整、引用有效和导出一致。可用、暂缓、剔除三个词池及 `04_kw_tagged.json` 只由工具导出。模型不得直接改词池绕过审查。不另做剔除词第二轮复核；最后复审实际使用的词及相关意图。

## 4. 卖点与字段安排

只生成新版标题＋独立灰字小标题结构。把买家需求、本品能力、证据和适用场景对应起来，形成有依据的 `intent_map`。按[标题](../knowledge/distilled/title_rules.yaml)、[五点](../knowledge/distilled/bullets_rules.yaml)、[长描](../knowledge/distilled/description_rules.yaml)、[Highlight](../knowledge/distilled/item_highlight_rules.yaml)与[后台词](../knowledge/distilled/search_terms_rules.yaml)规则写 `05_title_keywords.json` 和 `listing.json`。

`05` 的 high/relevant 代表词数组沿用上游兼容结构，合计最多 8 个，并非全部词库或全部投放词的数量上限；保留全部必保短语及 placement_plan。按[新版字段分工](japanese-listing.md)记录 `05.field_strategy`：标题按真实指标优先相关流量词，小标题优先 confirmed 数量／套装组成，材质尺寸在五点正文，五点逐条以相关词开头；按型号选购时通常用2–3条连续适配主题按组列全对象，规格型和通用耗材按真实选择条件或购买问题安排，不强制机型表；记录事实引用、分组和预期数量，其余条目保留必要规格；说明指标未知、缺事实和必要例外。日本站不发 QA 请求，初始化的 `06_qa.json` 继续保留 skipped 状态。至少整理 6 个本品买家问题，给定资料（含可信关键词）仍未表达的事实不补造，相关答案标为未声明，来源写本地资料整理，不伪造问答采集。


## 5. 图片、A+ 与覆盖反馈

按[图片规则](../knowledge/distilled/image_planning_rules.yaml)输出主图 1 张、附图 6 张、A+ 至少 5 张的制作策划。这是本技能默认交付范围；用户明确给定数量时按其范围调整数据与检查配置。保留来源素材、画面方向、买家问题、卖点、日文图上文字、事实／参数引用、对应正文位置及移动端检查。主图不叠加文案，素材不足写明需要补拍的内容。实际图像制作由后续图片工作完成。

```sh
python3 "SKILL/scripts/keyword_quality.py" coverage --run-dir "RUN"
```

结合 `07_keyword_coverage.json` 的未覆盖可用词、意图表达、后台字节和可补事实改进一轮，保持自然日语，不为覆盖率虚构用途。同时查看 field_layout 的标题／小标题字符数、标题词位和五条开头命中；这些是精确匹配提示，不能代替相关性判断。改动文案后同步更新 field_strategy，最后记录实际投放位置：

```sh
python3 "SKILL/scripts/keyword_quality.py" coverage --run-dir "RUN" --write-placement
```

画像后来变化时先 `rebind --run-dir RUN`，只审查工具列出的受影响词，更新结论后再执行 `rebind --run-dir RUN --reviewed`、check 和 export；不能只修改指纹。

## 6. 分层验收与报告

```sh
python3 "SKILL/scripts/listing_quality.py" check \
  --profile "RUN/01_product_profile.json" --listing "RUN/listing.json" \
  --qa "RUN/06_qa.json" --output "RUN/08_validation.json"
python3 "SKILL/scripts/listing_environment.py" config-status
```

首轮缺后端与独立复审时返回 incomplete 属正常状态，先处理本地 error。凭据存在且本地检查无错误后，执行后端校验；缺 token 则记录后端未验证，继续能做的复审与报告。新 token 仅通过 `listing_environment.py configure` 标准输入更新，默认复用用户目录中既有配置。

```sh
python3 "SKILL/scripts/listing_quality.py" backend \
  --profile "RUN/01_product_profile.json" --listing "RUN/listing.json" \
  --output "RUN/08_backend_validation.json"
python3 "SKILL/scripts/listing_quality.py" review-template \
  --profile "RUN/01_product_profile.json" --listing "RUN/listing.json" \
  --qa "RUN/06_qa.json" --output "RUN/08_semantic_review.json"
```

本品生成一个 validate 请求，只有内容相同且先前通过的本品记录可复用。独立复审者对照本品原始资料填写 identity、readability、factual_support、language、qa_relevance、keyword_usage、field_layout、compatibility_models 与 image_truth 的结论和依据。field_layout 须对照词表指标核对标题优先词、小标题数量事实、五点逐条开头与规格正文落点。compatibility_models 按画像的选购依据检查，以用户给定原词及语义角色记录为依据，不要求官网二次核验；核对画像是否漏录、五点是否漏项／多写、完整后缀及本品资料一致性。长清单不为250字符本地编辑预算删型号；后端若拒绝真实长度，保留完整稿与失败状态，不静默截断。无资料或无实际复审不能填 pass。有可用的独立 Agent 时委派一次复审，提供本品原始资料、最终内容与待复审记录，要求返回逐项依据；没有独立复审者时保持 pending 并说明缺口。

```sh
python3 "SKILL/scripts/listing_quality.py" check \
  --profile "RUN/01_product_profile.json" --listing "RUN/listing.json" \
  --qa "RUN/06_qa.json" --review "RUN/08_semantic_review.json" \
  --backend "RUN/08_backend_validation.json" --output "RUN/08_validation.json"
python3 "SKILL/scripts/render_listing.py" --run-dir "RUN"
```

某个证据文件未取得时从最终检查命令中省略对应参数，状态必须保留 incomplete／failed，不能创建假通过文件。验收最多修复两轮；改动后只重跑受影响校验与复审。三份最终文件为 `listing.md`、`listing.json`、`report.html`，失败或部分完成也输出已有结果与准确缺口。完整词池、原始输入、指纹和失败记录保存在任务目录。
