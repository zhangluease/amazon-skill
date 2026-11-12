# 本地数据契约 v2.7（Listing schema_version 仍为 "2.1"）

本地版本只执行 JP/Japanese/ja，主要面向耗材。用户附件名称任意；本文的编号文件名仅是任务目录内的脚本接口，不限制用户原文件命名。来源库中的历史文件名也不是必交附件。

本文是新增字段的唯一结构定义。已有单品字段继续有效；新生成内容使用 `schema_version: "2.1"`。每次只生成一个商品，多个商品使用独立任务目录；旧内容不能因可读取而被宣布通过当前验收。所有 JSON 使用 UTF-8。

## 产品画像：01_product_profile.json

保留 `site/listing_language/listing_language_code/category/audience/materials/functions/scenes/specs/limitations/pain_points`。新增：

- `brand`: 用户品牌字符串或 null；不造品牌。
- `product_identity`: `{original_name, canonical_name, protected_terms: [目标语言完整核心短语]}`。至少一个必保短语；比较只规范大小写、空白与连字符，不允许拆散名词短语。
- `facts`: `[{fact_id, field, value, source_ref, status}]`。`status` 为 `confirmed/unknown/conflict`；facts 仅记录本品事实。只有 confirmed 且有来源的事实能支持声明。
- 适配机型用 `facts.field=compatible_models`，value 为完整机型字符串数组，每个字符串一个型号，不能用系列名、范围、通配符或省略号。可按系列／来源分多条事实，另加可选 `group_label`；保留 source_ref 和 confirmed/unknown/conflict。事实只能支持本品。confirmed 必须有非空完整数组与来源，给定词表中的语义明确的适配对象标识直接按用户依据登记 confirmed；不因缺官网证明标 unknown。实际无法解析或资料冲突仍保留未完成状态。按[清单规则](../references/compatibility-lists.md)在可售商品五点列全确认型号，每条 claims 引用对应 fact_id；完整匹配允许大小写、全半角和连字符字形差异，不允许丢后缀。机器检查缺项／未引用，不证明兼容关系，也不能替代对照原件检查多写与漏录。
- 购买数量事实使用 field=quantity/package_quantity/unit_count/pack_count/number_of_items，value 为正整数或正整数字符串，必须与配件数量分开。首版机器核对英文 2-Pack、2 count、2 pieces、Pack of 2、Set of 2、Quantity: 2 等表达的数值、范围及事实引用；图片数量声明须在 body_refs 正文引用同一数量事实。其他语言表达和画面实物数量仍需语义复审。随附配件数量用 field=accessory_count/included_quantity/component_count（confirmed 正整数），`4 pcs felt pads` 这类写法据此核对，且不能满足购买数量；`N pieces of <物品>` 视为配件描述，不按购买数量扫描。
- `measurements`: `[{measurement_id, kind, subject, label, value, unit, source_ref, display_value?, display_unit?, display_text?, approximate?}]`。value 是原始十进制数字字符串；kind 为 `length/weight/volume/nominal/size`；subject 为 `product/package/fit`。label 标明 height/width/length/diameter 等。普通长度单位支持 mm/cm/m/in/ft。日本站保留来源单位，由 normalize 生成统一 display_text。公称规格和尺码不换算；禁止质量 oz 与容量 fl oz 混用。缺失值或单位不补造；需要表达的未知参数先补充资料。
- `images`: `[{image_id, source, applies_to: ["all"]}]`。source 是用户素材路径/URL/附件定位；只引用本品实际素材。
- `compatibility_basis`: 新任务按选购依据填 `model/specification/none`，另用 `compatibility_reason` 说明判断依据。型号适配、规格适配与无需型号适配的耗材分别组织五点，详见[耗材规则](../references/consumables.md)。旧画像缺此字段仍可读取；不得按编号前缀猜测，规格型与通用耗材不强制未知机型清单。
- `forbidden_terms`: 用户禁用词数组，可为空。
- `intent_map`: `[{relation, expression, fact_ids?, measurement_ids?, applies_to?}]`。relation 取 function/capability/activity/audience/location/season/body_part/companion/product_type/interest（COSMO 关系的精简映射）；expression 是目标语言的自然表达；引用必须 confirmed 且范围匹配（结构错误为 error）。表达未出现在任何可索引字段时给 warning `intent_unexpressed`；缺少 intent_map 给 warning。

fact_id、measurement_id 和 image_id 在本品画像中唯一。所有 applies_to 固定为 `["all"]`，只表示本品；不得混用其他商品事实或素材。

## 给定关键词的信任与适配登记

`02_kw_raw.json.source_kind=user_spreadsheets` 的给定词默认可信，不要求外部二次核验；来源列为官网、竞品或Amazon联想不降低信任。原词分组与角色仍留账，不重新以缺独立证明为由剔除。给定词本身可支持其明确表达的含义，不能由泛词推出未给出的数值、性能或认证。

`prepare` 生成 `02_identifier_roles.json`：`{schema_version:"1.0", raw_fingerprint, context_fingerprint, records}`。指纹由工具生成，records 每条 `{identifier, role, source_positions, reason}`；role 为 product_part／compatible_model／other／pending，source_positions 是 02.keywords 零基位置。按本品身份、字段标签和完整语义解释编号；脚本只发现中立候选和核对原文，不按前缀、长度或字母数字形式分类。可补纯名称、带空格、非拉丁文字的完整标识。同一标识在不同原词中角色不同可拆记录，同一行不重复分配；完整名称可覆盖其中的候选片段。未完成角色记录报 identifier_role_pending／identifier_role_missing，不要求外部验证。

完成角色后执行 `register-models --run-dir RUN`，追加／更新 `fact_id=trusted-keyword-models` 的 compatible_models confirmed 事实；confirmation_basis=user_provided_keywords，source_ref 指向原词与角色记录，keyword_source_positions 为 `{完整标识:[keywords零基位置,...]}`。重复来源全部保留，适配对象去重，完整后缀保留，本品／替代件标识和规格数值不进入适配数组。可直接解决的 unknown 占位事实转为 confirmed，保留原值和历史；不伪称独立验证。明确否定的适配关系不反转为正向兼容。

`register-models` 可修复旧任务中仅因缺网站证明而暂缓的适配对象，保留 initial_decision／initial_reason 并写 resolution。角色未完成或失效时不修改事实和账本；角色修改后重新登记，同步派生事实与受影响词。账本的 identifier_roles_fingerprint 绑定当前角色记录。画像另有未绑定修改时先 rebind；原词变化新建任务，不覆盖原始快照。修复后 export、更新五点并复审，不复用旧验收。检查对照原词及角色记录独立检查漏登（trusted_keyword_model_missing）和错误隔离（trusted_model_quarantined）；缺少整份 compatible_models 也不能绕过检查。角色判断的语义准确性仍须复审。

## 关键词与问题：02—06

- `02_kw_raw.json` 是 `import_keywords.py` 从用户表格生成的完整本地快照（结构见下文），不是后端扩词响应。历史 keywords/raw 结构继续可读。
- `03_keyword_decisions.json` 是关键词分类唯一依据，新版不生成或依赖 `03_keyword_review.json`；旧文件仅留作历史资料，详细结构见下文。
- `03_kw_filtered.json` 是原始词中 eligible 的字符串数组；`03_kw_removed.json` 是 excluded 的 `{keyword, reason, reason_code, keyword_id, applies_to}` 数组；新增 `03_kw_pending.json` 保存 deferred 并增加 `promotion_condition`。三者只由统一工具导出，补充词不混入原始词统计。
- `03_keyword_validation.json` 保存当前检查结果；总验收与报告重算数据，不信任此文件的缓存 passed。
- `04_kw_tagged.json` 保留原 keyword/label/reason；增加 `role`（identity/attribute/intent/synonym）、`intent_group`、`source`、`traffic_percentage`（未知为 null）、`applies_to`。旧 high/relevant 标签用于兼容，不决定核心品名优先级。该数组由统一工具导出，仅含 eligible 原始词及补充词，另有 keyword_id、origin=raw/product、searches、restrictions；未知流量为 null，0 保持真实的 0。
- `05_title_keywords.json` 保留 `title_keywords: {high: [], relevant: []}` 供原 CLI；增加 `protected_terms`、`placement_plan: [{keyword, target_field, applies_to, reason}]`。这些是候选与实际投放安排，不是全部进标题的配额。title_keywords 同时是 QA 请求词，high+relevant 去重后 ≤ quality_policy.qa_max_keywords（8），超出时 backend_cli 本地拒绝且不发请求。最终 placement_plan 由 `keyword_quality.py coverage --write-placement` 按最终文案的完整短语位置生成（字段如 title、bullets[2]、description）。high/relevant 数组必须存在，placement_plan 非空并逐项填写 keyword/target_field/applies_to/reason；protected_terms 至少覆盖画像中的全部必保词。
- `05.field_strategy`: 仅含 target=single 一项的数组；每项 `{target, title, item_highlight, bullet_openings, specifications, exceptions}`。title 为非空中文说明，记录实际标题词、指标来源与选择／取舍；item_highlight 说明包装数量及事实依据，未知时说明缺口；bullet_openings 为逐条列出开头词与主题的 5 个非空字符串；specifications 说明材质尺寸在对应五点的落点或缺失依据，适配型耗材同时记录机型事实、分组、预期数量和对应的清单条目；exceptions 为例外及具体依据的字符串数组，无例外为空。只作为内部布局解释，不进入上架字段。coverage --write-placement 保留此说明，但改文案后须同步更新，再做 field_layout 复审。
- `06_qa.json` 固定日本站跳过后端问答：status=skipped、site=JP、reason_code=rufus_us_only、qa_pairs=[]。自行整理的买家问题记录为 local_editorial，不冒充后端采集。

### 用户表格导入结构（import_schema_version: "1.0"）

`00_product_input.json.keyword_files` 记录全部用户输入文件的绝对路径；文件名任意，按格式和表头识别；先理解表格的站点／日期含义，不凭文件名决定输入用途或内容。`01_product_profile.json` 不要求竞品 asins；本品 SKU／ASIN 按用户资料保留，可为空。

命令：`python3 scripts/import_keywords.py --files FILE1.xlsx FILE2.csv --site SITE --run-dir RUN`。可选 `--sheet NAME`（可重复）、`--keyword-column LABEL`、`--searches-column LABEL` 和 CSV/TSV 的 `--encoding`。工具不使用凭据、不调用网络，也不执行 CLI parse-keywords。旧 XLS 需先转换为 XLSX。

`02_kw_raw.json` 字段：

- `source_kind: "user_spreadsheets"`、`import_schema_version: "1.0"`、`site`。
- `source_files`: 按输入顺序保留 `{file_id, filename, path, sha256, sheets}`。每表包含 name、visibility、status、imported_rows、blank_keyword_rows、repeated_header_rows；已导入表增加 header_row、columns（列号从 1 开始）、preamble_rows。未指定 --sheet 时，找不到关键词表头的非空工作表记为 status=no_keyword_header，附 preview（前 3 行，每格最多 40 字符），并登记 sheet_without_keyword_header；同一文件中没有任何可识别的工作表时导入失败。显式 --sheet 选中的表找不到表头时报错；未选中的表记为 not_selected，不静默遗漏。
- `keywords`: 所有数据行的 `{keyword, source: {file_id, sheet, row}, raw_cells, monthly_searches, traffic_percentage, keyword_translation, flow_type}`，保留输入顺序、原词和重复行。`raw_cells` 保存全部列（含未识别业务列）的 column/header/value/cell_type/formula/percentage_format；不丢弃额外指标。row 是源工作表行号，CSV 多行单元格取起始物理行。
- `raw.keyword_data`: 供筛词、覆盖率和报告读取的唯一词指标视图，每项包含 keyword、source_positions（keywords 的零基位置）、source_file_count（出现在几个来源文件中）、monthly_searches、traffic_percentage、traffic_percentage_max；识别到对应列时还包含 monthly_purchases、purchase_rate、aba_rank、title_density、natural_rank_best。关键词级指标（月搜索量、月购买量、购买率、ABA 排名、标题密度）多个非空值相同时保留，不同时为 null 并登记 metric_conflict。竞品 ASIN 级指标（流量占比、自然排名）各来源不同属于正常情况，不登记冲突：traffic_percentage 只在各来源一致时有值，另给 traffic_percentage_max 和 traffic_percentage_by_source；natural_rank_best 取各来源最小值。附加指标只用于排序和提示，不作为剔除依据。未知流量不是 0，真实 0 不改写；未确认的单位或数字格式不猜测。
- `import_issues`: 数值歧义、无公式缓存、指标冲突、说明页（sheet_without_keyword_header）、文件名站点不一致（filename_site_mismatch，仅提示）及对应来源位置。命令行只输出 import_counts、按 code 汇总的 issue_counts、前 5 条 issue_examples 和各工作表状态，完整清单只保存在 02 中。公式不执行，只读取已有缓存；关键词列为错误或公式无缓存时整个导入失败，指标错误时保留词并标 null。
- `import_counts`: files、raw_records、unique、duplicates，满足 raw_records=unique+duplicates。空关键词行、表头及明确排除的说明表不计关键词记录，但来源中保留位置。没有任何关键词时失败。
- `import_policy`: semantic_filtering=false、row_limit=null、deduplicate_input=false、conflicting_metrics=unknown、asin_specific_metrics=per_source_values_plus_max_or_best、extra_metrics_use=ordering_only。不按字符集、流量、流量类型或条数预筛选，完整意图判断统一发生在 03 阶段。

输出原子写入；已有 02 文件时拒绝覆盖。无效或部分失败的导入不能推进筛词。03 的 source_positions 可以精确回溯到本地原文件/工作表/行，审核指纹继续绑定整个 02 快照；输入改变创建新任务，不手改哈希宣称审核有效。示例见 [keyword_import_examples.json](keyword_import_examples.json)。

report.html 只嵌入 02 的 keyword_data、来源文件和工作表状态、计数和问题计数，不嵌入逐行 raw_cells，避免报告体积膨胀；完整数据保留在 02 文件中。

### 关键词分类结构（keyword_schema_version: "1.1"）

带明确产品事实的判断示例见 [keyword_examples.json](keyword_examples.json)，不要把示例事实当成本次商品事实。

此版本独立于 Listing 的 schema_version 2.1，不改变后端接口。工具命令均使用 `--run-dir RUN`：prepare 建草稿；check 检查分类及已有导出/下游数据；export 从通过检查的分类记录生成三个词池及 04 标注。prepare 不自动完成语义分类；没有额外的剔除词第二轮复核命令或强制步骤。

`03_keyword_decisions.json`：

- `source_fingerprints`: 01_product_profile.json 和 02_kw_raw.json 的字节 SHA-256，工具生成。画像或原词改变后先逐条重新核对，再更新绑定并重新校验；不单改哈希冒充重新审查。
- `records`: 按原始词首次出现顺序排列的唯一词记录。工具生成且不得改写 `keyword_id/keyword/normalized/source_positions/original_forms/origin`；source_positions 为原始 keywords 数组的零基位置。重复记录保存所有位置和原词，norm 保留词序、否定词、数字和单位。
- `bound_facts`: 工具生成的逐事实摘要，用于 `rebind` 精确列出受画像改动影响的词。
- `intent_groups`: 由 `apply` 写入的意图组判定留档（组字段 + 成员 keyword_id）。逐词记录仍是唯一分类依据。
- `supplemental_terms`: 单独登记本品新增表达。字段同逐词记录，但 origin=product、source_ref 为真实来源，无原始词位置。编号为 `product-` 加规范化词的规范 JSON SHA-256 前 20 位；核心名称由 prepare 预登记。不能通过新增同词补充记录绕过原词的暂缓或剔除。

每条记录需填写：

- `initial_decision`：初次 eligible/deferred/excluded；`decision`：最终分类，同样三个值。null 表示尚未审查，不能完成验收。原始词必须全部且仅归一类。
- `reason_code` 对应当前 `decision`：eligible 使用 identity/attribute/intent/synonym；deferred 使用 ambiguous_intent/unknown_fact/seasonal_unconfirmed/identity_conflict；excluded 使用 product_mismatch/fact_conflict/user_restriction/unusable_query。该代码不是机器自动分类结果。
- `reason/query_intent/evidence/reviewer`：非空字符串，分别解释当前分类的具体理由、完整搜索意图、本品证据及真实执行者。不得只写“无关”“白名单外”或通用套话。
- `role`：identity/attribute/intent/synonym；`label`：high/relevant；`intent_group`：同意图分组。组内不同分类时，每项补 `group_difference` 说明真实差异；不应以另建同义组掩盖冲突。
- `fact_ids/measurement_ids`：无重复引用数组；仅限本品事实和规格；`identity_basis` 为布尔值，仅在产品身份本身足以支持相关性时填写 true。eligible 引用的事实必须 confirmed 且有 source_ref，原始给定词本身也可支持它明确表达的属性，不要求再次提供说明书；产品补充词仍须有事实或规格依据。
- `applies_to`：固定 `["all"]` 表示本品；`restrictions`：使用限制字符串。
- `promotion_condition`：deferred 必填，说明疑点与晋级所需事实；已审查的 deferred 可以留档，不使审查阶段失败。核心品名明确冲突时用 identity_conflict 暂缓，并保持未完成，不能静默丢弃。
- `resolution`：初次与当前分类不同时必填；保留原初次判断，且同步更新当前 reason_code/reason/evidence。可用 initial_reason 另行保存旧理由，不用旧理由导出当前词池。`matched_terms` 为可选的完整词/短语命中证据数组，只校验词边界，不决定相关性。

意图组判定（新增，不替代逐词账本）：`keyword_quality.py apply --run-dir RUN --groups FILE`，FILE 为
`{"reviewer": "...", "groups": {"组id": {decision, reason_code, reason, query_intent, evidence, role, label, fact_ids, measurement_ids?, identity_basis?, applies_to, promotion_condition?, restrictions?, keywords: [原词或 keyword_id]}}, "overrides": {"原词或 keyword_id": {要单独改写的字段, group_difference, resolution?}}}`。
工具把组字段复制到每个成员（intent_group=组id、reviewer、initial_decision 为空时取 decision），未列出的词保持不变，可分批执行；未知词或一个词进入两个组时报错且不写入。之后 check 的全部逐词规则照常生效。`03_keyword_view.tsv` 是只读紧凑表（keyword/searches/traffic_pct/sources/purchase_rate/best_rank/decision/group/cluster/origin），按搜索量、覆盖竞品数、购买率排序，由 prepare/view/apply 生成；指标缺失时为空，只用于阅读和排序。`view --clusters` 另写 `03_keyword_clusters.json`（`{raw_sha256, clusters: [{cluster_id, head, keyword_ids, searches_total}]}`）：head 是被至少 3 个其他查询包含、且本身也是查询的短语（不超过 4 词），每个查询归入它包含的最长 head，没有 head 时按末词归组。这只是词面候选分组。groups 的 keywords 可以写 `"@c001"` 引用整组（要求 raw_sha256 与当前 02 一致），用 `except` 排除个别词；分类仍由逐组语义判断决定，全部逐词检查不变。
画像在筛词后被修改时运行 `rebind`：原词变化则拒绝；否则列出引用了变化事实（或身份、禁用词变化相关）的词，核对后 `rebind --reviewed` 重新绑定；无受影响词时直接绑定。

事实冲突不能引用 unknown/conflict 事实作为已证实反证。user_restriction 必须对应画像中用户明确提供的 forbidden_terms；不能自造禁词。unusable_query 只用于没有有效文字或数字的查询，有有效内容时先修复或暂缓判断。

旧版兼容：可读取 1.0 分类记录，但也须满足当前理由与 decision 一致的要求。旧 03_keyword_review.json 的缺失、过期、结论或格式不参与当前校验及导出；不自动把旧复核结论合入分类记录。沿用历史修正时，将有依据的当前理由明确写回 03_keyword_decisions.json，再导出。旧报告继续可读，缺当前分类或最终文案复审时不能仅凭历史通过状态宣布新版验收完成。

三个词池和 04 标注只从当前 decision、reason、reason_code 导出；改变分类时的初次判断与 resolution 保留追溯。自动统计：原始记录数＝唯一词数＋重复记录数；唯一词数＝eligible＋deferred＋excluded；补充词另计。完整任务的关键词阶段还校验导出一致性、05 候选/投放范围、QA 请求词和后台中的完整暂缓/剔除词组。较短歧义短语被有依据的完整词组覆盖时不按子串误伤。

自然正文、图片和后台倒装表达的语义由最终 `keyword_usage` 复审负责，核对实际采用的词及相关同意图表达，不要求将全部未采用或剔除词再审一遍。field_layout 另按新版字段分工逐项复审词位、包装数量、五点开头和规格落点；该复审不通过词袋交集自动认定违规，也不能用程序检查代替语义复核。最终语义复审额外保存 `keyword_fingerprints`，绑定原词、编号角色、分类和 05 投放计划；修改这些文件后须重新复审关键词使用、字段布局及适配对象。

## 最终内容：07_listing.json

公共字段：schema_version、site、listing_language、listing_language_code、brand_name（字符串或 null）、buyer_question_coverage（数组）、excluded_claims（字符串数组）、attribute_suggestions（数组）。

`attribute_suggestions` 每项 `{attribute, value, fact_ids, measurement_ids?, applies_to?, note?}`：后台结构化属性建议（item type、material、target audience、intended use、special feature、included components、number of items 等），值必须引用 confirmed 事实或规格；缺字段给 warning。buyer_question_coverage 少于 6 项给 warning `question_matrix_thin`（5W1H + 品类常见问题）。

`buyer_question_coverage` 每项 `{question, status, location, source?}`；status 使用 covered/partially_covered/not_claimed/not_applicable。location 用中文说明真实覆盖字段或未主张原因。自行整理的问题标 source=local_editorial，不包装成已采集问答。

单品继续使用顶层 `title/item_highlight/bullets/description/search_terms`；item_highlight 永远是字符串，bullets 是 5 条 `【相关搜索词／主题标签】正文` 字符串，开头自然承接相关可用词，正文展开事实，不强行大写。适配型耗材通常连续2–3条按系列列全已确认机型（短名单可1条），不强求五个不同主题。普通材质尺寸默认在其余五点正文，小标题优先真实数量与组成；标题／小标题分别 ≤75／125 字符，含空格。另加 claims、image_plan、a_plus_plan。

### 声明与图片引用

本品 `claims` 是数组：`{field, fact_ids: [], measurement_ids: []}`。field 支持 title、item_highlight、bullets[0] 至 bullets[4]、description、search_terms。每个前台文本字段至少有一个声明记录，允许无可核查数值的场景文字用空引用并由语义复审说明；非空引用必须属于本品且已确认。含规格的字段记录 measurement_ids 并复用 display_text，保持维度标签；后台搜索词不强制声明记录。

### 图片和 A+ 策划

新输出使用 `image_plan` 和 `a_plus_plan` 两个直接数组，不需要额外的方案分组或参数替换层。每个 plan_id 唯一，不能重复创意虚增数量。

- `image_plan`：按顺序 1 张主图＋6 张附图，role=main/secondary，image 从“图1（主图）”到“图7（主题）”连续编号。
- `a_plus_plan`：至少 5 张图片，可附文字 FAQ 等模块；role=aplus，asset_type=image/text，module 为“模块N：主题”，连续编号。纯文字模块不计入图片数量。
- 各项结构：`{plan_id, role, image? 或 module?, direction, applies_to, buyer_question, selling_point, visual_evidence, scene_intent, overlay_text, native_text?, fact_ids, measurement_ids, source_image_ids, body_refs, mobile_check, production_notes, status?, asset_type?}`。
- applies_to 固定 `["all"]`。direction、画面证据、制作说明等用中文；overlay_text 是日文嵌字，native_text 是可选日文 A+ 原生文案，缺省为空。主图两种文案均为空。
- status 为 ready/pending；ready 只表示策划依据具备。缺事实或素材时用 pending 并在 production_notes 说明，验收保持 incomplete；不能宣称已验收不存在的成图。
- fact_ids、measurement_ids 和 source_image_ids 只引用本品资料。需要实物图但 source_image_ids 为空时保留缺口并使验收 incomplete；纯文字模块不要求实物素材。
- body_refs 定位本品的 title、item_highlight、bullets[0] 至 bullets[4]、description。图文参数复用 measurement.display_text，并在对应正文中表达和引用同一规格／数量事实。
- mobile_check 写清手机阅读的文字量、字号层次、主体比例等要求。引用检查只验证已登记数值和范围，语言、画面、场景与兼容性事实仍需语义复审。
- 旧 `aplus_plan` 别名仍可读取；与 a_plus_plan 同时存在但不一致时明确报错。

## 验收与后端适配

- `08_semantic_review.json`: 复审记录。新版写 `target_fingerprints`（每个 target 与 media 依赖内容的规范 JSON SHA-256；重新格式化文件不失效）和 `carried_over`；review-template 遇到已有复审文件时保留内容未变 target 的结论（keyword_usage、field_layout 还要求关键词指纹未变），只把变化的 target 置为 pending（--fresh 可全部重置）。旧版只有文件级 fingerprints 时按文件 SHA-256 绑定。目标仅 single 和 media。每项 `{target, check, status: pass/fail/not_applicable/pending, evidence}`。文本检查 identity/readability/factual_support/language/qa_relevance/keyword_usage/field_layout/compatibility_models；media 检查 image_truth。not_applicable 需要原因；pending/fail 或缺项不能交付通过。compatibility_models 按原始适配资料核对画像录入及五点的完整性、额外机型、后缀、分组、来源和本品一致性；无适配需求可说明原因记 not_applicable，待核实清单不能冒充不适用。不得为了过关机械填 pass。
- `08_backend_validation.json`: `{records: [{target, payload_sha256, exit_code, response, reused}]}`；重跑 backend 时，本品 payload 未变且已通过时沿用旧记录（reused=true），--no-reuse 强制重新请求。target 固定为 single；response 保留后端 JSON，仅 `ok=true` 且 `errors=[]`、退出码为 0 才通过。响应或 payload 指纹缺失/过期不通过。
- `08_validation.json`: 汇总本地、关键词审查、语义、后端检查（local/keywords/semantic/backend）；`status` 为 passed/failed/incomplete；fingerprints 绑定 profile/listing/qa 原文件 SHA-256，evidence_fingerprints 的 review_sha256/backend_sha256 绑定复审与后端文件的规范 JSON SHA-256，keywords_sha256 绑定重新计算的关键词阶段结果 SHA-256（UTF-8、ensure_ascii=False、sort_keys=True、separators=(',',':')）。报告核对原验收文件存在且指纹匹配；缺失、过期为 incomplete，不以生成了文件代替完成。

`07_keyword_coverage.json`（`keyword_quality.py coverage` 生成，仅供优化参考，不参与验收）：本品 target=single 的可用词数、完整短语覆盖、词级覆盖（大小写与简单英文复数折叠）、按搜索量加权覆盖率、未覆盖高流量词、标题覆盖前 10 词、后台词字节/预算/与标题重复词、暂缓或剔除词的完整短语命中；field_layout 列出标题／小标题字符数、可用前十词的标题零基精确位置（缺失为 null）、每条五点前 80 字符及精确短语命中（不代表同义表达无效或算法权重）；intent_map 表达位置；按搜索量排序的 fact_unlocks（暂缓词晋级条件）与 unsupported 问题。

本地检查的 warning（不阻断）：title_word_repeat、search_terms_title_repeat、search_terms_duplicate、search_terms_format、bullet_prohibited_symbol、intent_unexpressed、intent_map_missing、attribute_suggestions_missing、question_matrix_thin、compatibility_list_length（已确认型号超普通预算，原文仍须过真实后端／类目检查）。compatibility_unconfirmed 为 incomplete。error：compatibility_model_missing、compatibility_unreferenced、compatible_models_shape、title_banned_character（品牌名中的除外）、search_bytes（JP 预算 499 UTF-8 字节）。

关键词旧任务修复命令：`python3 scripts/keyword_quality.py register-models --run-dir RUN`，修复后再 export、重写文案并重新验收。

本地工具命令统一使用 `python3 scripts/listing_quality.py`：

1. `normalize --profile FILE --output FILE`：计算展示值并输出画像（允许原位原子替换）。
2. `check --profile FILE --listing FILE [--qa FILE] [--review FILE] [--backend FILE] --output FILE`：只检查，不自动改文案；缺验收为 incomplete，退出非零。
3. `prepare --profile FILE --listing FILE --output-dir DIR`：检查可确定的结构后，导出 single.json 与 manifest.json，不发送请求。
4. `review-template --profile FILE --listing FILE [--qa FILE] --output FILE`：生成绑定指纹的 pending 清单，供独立语义复审填写。
5. `backend --profile FILE --listing FILE [--cli CLI_PATH] [--config FILE] [--timeout SECONDS] --output FILE`：生成本品单一 payload，通过 backend_cli.py 统一读取配置并调用原 CLI validate；无需调用者设置环境变量。捕获脱敏业务 JSON 及原进程退出码；启动/配置错误的 exit_code 为 null，并附 error_code、failure_reason。默认超时 120 秒。配置和二进制内容不修改。

后端统一入口 `python3 scripts/backend_cli.py`：

- `qa --keywords-file FILE --site US --output FILE`：仅从 CLI 的完整临时输出读取 JSON，脱敏后原子写入原有结构，不把终端摘要当作原始数据；title_keywords 超过 8 个时本地拒绝且不发请求。本表格版不提供 expand，原词用 import_keywords.py 导入。
- `validate --listing-file FILE --site SITE [--output FILE]`：原 CLI 单品接口；无 output 时返回的安全摘要含 response。本品验收使用上述 listing_quality.py backend。
- 两个命令均可选 --config、--cli、--timeout；凭据只能从配置文件读取，无环境变量回退或 token 命令行参数。默认配置相对 Skill 定位，不依赖工作目录；自动选择本 Skill tools/bin 中的 laochen-cli-v2 平台文件。
- 非零进程退出、无有效 JSON、超时和显式业务失败均返回非零；validate 仍要求 ok=true 且 errors=[]。进程已执行时保留真实 exit_code，未执行/未正常返回时为 null。错误响应可留档，不代表验收通过；无新有效响应时旧文件不覆盖、不视为本次结果。原始进程日志不透传。

报告工具 `python3 scripts/render_listing.py --run-dir DIR` 从同一 JSON 生成 07_listing.md 与 report.html。

- Markdown 单品依次为 Title、Item Highlight、Bullet Points、Description、Search Terms、附图策划、A+ 整体策划、买家问题覆盖清单。只显示文案、中文创意说明和必要产品信息，不放 claims、画像、指纹、校验 JSON。
- HTML 保持示例的报头、摘要漏斗、七个章节和白底 Listing 文档卡片；标题与灰字小标题在同一预览区域依次展示，下附各自字符计数，不将两段合并；完整图片创意用标题和自然段展开。禁止新增导航条、验收面板、原始 JSON 或逐图技术字段列表；图片保留画面说明、目标语言文案及必要的制作前待确认事项。技术引用和完整验收记录存入内嵌 JSON，摘要仅显示简短状态；缺失或旧版校验不能标通过。HTML 的简洁展示不删改结构化 JSON 或 Markdown 的内容。
- 最终固定链接 07_listing.md、07_listing.json、report.html 三份文件；中间采集/校验文件保留追溯，不另建“文案预览”。
