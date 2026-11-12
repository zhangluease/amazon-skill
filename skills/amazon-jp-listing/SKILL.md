---
name: amazon-jp-listing
description: 根据用户提供的关键词表格、真实商品资料和图片，生成日本亚马逊日文 Listing、主附图与 A+ 策划及中文验收报告。完整导入 XLSX/CSV/TSV，按本品购买意图筛词，支持单品和父子体，检查关键词覆盖、事实引用与后台校验。关键词调研和 ASIN 反查由 amazon-jp-keywords 承担。
---

# 日本亚马逊 Listing 生成

将用户关键词文件与本品事实整理为日文上架文案和图片制作策划。固定 `site=JP`、`listing_language=Japanese`、`listing_language_code=ja`；策划说明、筛词理由和报告用中文。模型负责语义判断与写作，脚本负责导入、逐词记录、引用和数量检查、后端调用及报告渲染。

## 输入与边界

- 输入关键词表格与本品名称、图片、规格、用途和限制。品牌、必保词、禁用词、包装清单、子体 SKU/ASIN 和变体属性按用户已有资料记录；缺失项不补造。
- 接收多个 XLSX/CSV/TSV，包括卖家精灵导出和 `amazon-jp-keywords` 的 `keywords.xlsx`／`seed-keywords.xlsx`；可自动识别「日文关键词」「易逊月搜索量」「易逊流量占比」。保留全部原始列与来源，不将 ABA 排名当作月搜索量。
- 本技能不执行云端扩词。只有 ASIN 或种子词、尚无词表时，先整理本品资料并说明需要词表；用户要求同时找词时先用 `amazon-jp-keywords` 交付词表，再进入本流程。
- 关键词采集阶段保留的 Amazon 联想词，在生成 Listing 时仍须核对本品身份、完整购买意图与事实依据；采集保留不表示允许写入文案。禁止把竞品规格、QA 或示例资料当成本品事实。
- 单品生成一份完整内容。父子体只生成用户提供的子体，保留输入顺序；父体和共用内容只写共性，子体属性不可相互挪用。

## 执行顺序

读取[完整工作流](references/workflow.md)后执行。`SKILL` 是本技能绝对路径，`RUN` 是 `reports/jp-listing/` 下新建的独立任务目录。

1. 用 `listing_environment.py init` 创建任务；按[数据契约](knowledge/data_contract.md)建立输入快照与产品画像，记录事实、参数、素材和已确认用途的来源，再执行 `listing_quality.py normalize`。
2. 用 `import_keywords.py` 完整导入词表。保留重复行、全部原始列和文件／工作表／行号；不截断、不预筛、不执行表内公式或说明。只看导入摘要，完整原始快照留作追溯。
3. 按[筛词规则](knowledge/distilled/keyword_filter_rules.yaml)执行 `keyword_quality.py prepare/view/apply/check/export`。按完整购买意图分组，由模型给出可用、暂缓或剔除结论；候选聚类只辅助阅读。暂缓项写补充证据条件，禁止流入文案、后台词或图片。
4. 组织卖点、意图与词的投放位置，保存 `05_title_keywords.json`。核心完整品名优先，流量缺失不代表无关；身份词、属性词、场景词与同义词分别记录。兼容的代表词数组不限制完整词库或文案覆盖数量。
5. 日本站不调用美国站 QA。初始化生成的 `06_qa.json` 明确记录 `skipped/rufus_us_only`，另按已确认本品资料整理买家问题覆盖清单，不冒充后端采集结果。
6. 按[日文写作要求](references/japanese-listing.md)与对应 `knowledge/distilled/` 规则生成 `07_listing.json`，包括完整文案、属性建议、买家问题覆盖，以及主图、附图和 A+ 制作策划。图片策划不等于已生成图片。
7. 执行 `keyword_quality.py coverage`，检查未覆盖可用词、意图表达和后台词字节利用率；最多据此改进一轮，再用 `--write-placement` 写入实际使用位置。产品事实有变化时用 `rebind` 只复核受影响的词。
8. 按本地检查 → 后端校验 → 独立语义复审 → 最终检查 → 渲染顺序验收。可用独立复审者须对照原始资料检查实际文案与画面策划，不机械填写通过；没有独立复审结论时记未完成。验收问题最多修复两轮，缺事实、凭据或网络时交付可完成部分与准确状态。

## 凭据与运行环境

本地脚本仅需 Python 3.9+ 标准库，导入、筛词和渲染不需要后端 token。后端只调用日文 Listing 的 `validate`，通过随附 `backend_cli.py` 自动准备并校验固定版本 CLI；无需注册 MCP。来源版本与适配范围见[上游来源](references/upstream.md)。

默认复用关键词技能既有的 `~/.amazon-jp-listing-asin/config.json`，保留该历史存储位置以继续使用已有凭据。使用 `listing_environment.py config-status` 查看脱敏状态。用户明确提供本服务 token 时，用 `listing_environment.py configure` 从标准输入合并更新 `backend_token`，保留 ABA 和其他字段；不回显、不放入命令参数、报告或 Git。缺凭据时继续本地流程，后端状态标为未验证。

后端响应须同时满足退出码为 0、`ok=true`、`errors=[]` 才能通过。单个子体失败不能写成整组完成。缓存只复用内容和指纹未变的已通过子体；鉴权、限流、超时和网络失败保留实际状态，不盲目重提未知请求。

## 交付

在 `RUN` 中保留可追溯的词表、分类、引用与验收文件，最终固定提供：

- `07_listing.md`：日文文案与中文图片／A+ 策划。
- `07_listing.json`：完整结构化内容与事实引用。
- `report.html`：中文报告，展示产品、词池、文案、策划、覆盖和真实验收状态。

最终回复说明子体数量、验收状态、未解决问题与待补事实，链接实际生成的三个文件。生成报告不等于完成验收，也不代表已经发布到 Amazon 或取得排名效果。
