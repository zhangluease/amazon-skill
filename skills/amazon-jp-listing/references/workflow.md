# 从输入到交付

`SKILL` 为技能绝对路径，`RUN` 为新建任务目录。输入文件名不限；编号文件是内部接口，结构见[数据契约](../knowledge/data_contract.md)。

## 1. 画像与导入

```sh
python3 "SKILL/scripts/listing_environment.py" init --run-dir "RUN"
python3 "SKILL/scripts/import_keywords.py" --files "词表.xlsx" "补充词表.csv" --run-dir "RUN"
```

根据本品资料写 `01_product_profile.json`，再规范化参数：

```sh
python3 "SKILL/scripts/listing_quality.py" normalize --profile "RUN/01_product_profile.json" --output "RUN/01_product_profile.json"
```

画像记录事实、参数、素材、禁用词与选购依据；给定适配表逐项保存。表格完整导入，保留重复行、全部列和原始位置。中／英／日文常见表头可自动识别，歧义列用 `--keyword-column`／`--searches-column` 指定；CSV/TSV 默认 UTF-8，UTF-16 BOM 可识别，旧 XLS 须转换。公式只读缓存。指标冲突保留原值、聚合记未知；ABA 排名不当月搜索量。

## 2. 编号与词池

```sh
python3 "SKILL/scripts/keyword_quality.py" prepare --run-dir "RUN"
```

prepare 已生成 `03_keyword_view.tsv` 和编号候选。按[耗材规则](consumables.md)完成 `02_identifier_roles.json` 的语义角色与理由，可补完整名称型标识；角色待填或有新增适配对象时运行：

```sh
python3 "SKILL/scripts/keyword_quality.py" register-models --run-dir "RUN"
```

按[关键词规则](../knowledge/distilled/keyword_filter_rules.yaml)写 `03_keyword_groups.json`。词多时可选 `view --clusters` 辅助阅读，聚类不作分类结论。

```sh
python3 "SKILL/scripts/keyword_quality.py" apply --run-dir "RUN" --groups "RUN/03_keyword_groups.json"
python3 "SKILL/scripts/keyword_quality.py" export --run-dir "RUN"
```

export 自带分类与导出检查；三个词池和 04 标注由工具生成，不需在它之前重复运行 check。

## 3. 写作与覆盖

按[日文字段规则](japanese-listing.md)写 `05_title_keywords.json` 和 `listing.json`。05 记录候选、必保词、实际投放与 field_strategy；候选数量按实际需要安排。主题采用SOP默认顺序，长适配名单按每条全文 250 字符分组，位置不足时优先让出独立性能点，并在 exceptions 记录取舍。若五条仍超限，保留完整草稿和失败状态。标题记录本品品牌、前置目标品番、完整部件名、适用对象和兼容关系的依据。小标题先为包装内容预留字数，记录所选 3 个已确认机型，末尾集中一次写装数与组成。仅使用已给定竞品结论辅助选题，不追加调研前置条件。文案还包含属性建议、本品关键买家问题及图片／A+ 策划；资料没有答案时记未声明，不凑问答数量。

```sh
python3 "SKILL/scripts/keyword_quality.py" coverage --run-dir "RUN" --write-placement
```

该命令同时生成覆盖反馈和实际投放记录。查看未覆盖词、标题前 40 字符、五点开头及全文字符数、意图与后台字节，必要时改文案并同步 field_strategy，再重跑；不为覆盖率堆词。完整短语匹配不代表同义表达无效。

## 4. 验收与渲染

先检查本地问题：

```sh
python3 "SKILL/scripts/listing_quality.py" check --profile "RUN/01_product_profile.json" --listing "RUN/listing.json" --qa "RUN/06_qa.json" --output "RUN/08_validation.json"
```

本地无错误且配置可用时调用后端；缺凭据或请求失败保留实际状态，不盲目重试。

```sh
python3 "SKILL/scripts/listing_quality.py" backend --profile "RUN/01_product_profile.json" --listing "RUN/listing.json" --output "RUN/08_backend_validation.json"
python3 "SKILL/scripts/listing_quality.py" review-template --profile "RUN/01_product_profile.json" --listing "RUN/listing.json" --qa "RUN/06_qa.json" --output "RUN/08_semantic_review.json"
```

对照原资料和最终内容填写复核依据：品名、语言、事实、实际用词、字段布局、适配清单与画面，以及七个前台位置的数量／关系一致性、性能／更换／品质声明的依据、模板占位符和教学文字清理。确有未解决问题时保持 pending／fail，不机械填 pass；独立复审者按需要使用。后端通过要求退出码 0、ok=true、errors=[]。

```sh
python3 "SKILL/scripts/listing_quality.py" check --profile "RUN/01_product_profile.json" --listing "RUN/listing.json" --qa "RUN/06_qa.json" --review "RUN/08_semantic_review.json" --backend "RUN/08_backend_validation.json" --output "RUN/08_validation.json"
python3 "SKILL/scripts/render_listing.py" --run-dir "RUN"
```

未取得的后端／复核文件省略对应参数，交付状态保留未完成或失败。日本站不采集后端 QA；init 自动保存的 06 文件只是兼容标记，存在时沿用，没有该标记也不要求额外采集。最终三份文件为 listing.md、listing.json、report.html。

## 配置与旧任务

- `listing_environment.py config-status` 查看脱敏配置。用户提供 token 时，用 configure 从标准输入更新公共配置，保留其他字段；凭据不放入命令参数、报告或 Git。固定版本 CLI 按需准备，见[上游适配](upstream.md)。
- 画像变更后先 `keyword_quality.py rebind --run-dir RUN`，只处理工具列出的受影响词，再用 `rebind --reviewed` 绑定。原词变更新建任务，保留旧快照。
- 编号上下文变化时先将旧角色记录移出 RUN，运行 register-models 生成当前候选并重新解释，再 register-models、export。旧任务仅因缺外部证明而暂缓的适配对象可由 register-models 修正，保留原结论和修正依据。
- 文案、来源或词位改动后重新完成受影响的覆盖、检查和复核；写作或校验规则变更时同步 quality_policy，其变化会使旧复核和报告过期，不复用旧通过状态。仅改日期／版本元数据不要求重审。
