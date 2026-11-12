# 上游来源与本地适配

本技能基于用户指定的 [老陈 lc_amazon_listing（用户表格版）](https://github.com/chenqinglin2019-tech/Laochen-Amazon-project/tree/87dead1472eb4ddfd28f99fd47a9593b5c3f7771/skills/lc_amazon_listing) 建立。

固定版本：`87dead1472eb4ddfd28f99fd47a9593b5c3f7771`。下列本地资源复用该版本并保留其行为：

- `scripts/import_keywords.py`：完整表格导入、来源追溯与指标冲突处理。
- `scripts/keyword_quality.py`：逐词分类记录、意图组、词池、覆盖检查与变更绑定。
- `scripts/listing_quality.py`：产品／子体／媒体结构检查、后端 payload 与独立复审指纹。
- `scripts/backend_cli.py`：凭据隔离、脱敏、业务响应检查与固定平台 CLI 调用。
- `scripts/render_listing.py`、`tools/listing_report_template.html`：离线文案与七章节 HTML 报告。
- `knowledge/`：数据契约、写作规则、策略配置和来源记录；未打包上游大体积产品示例。

本地适配：只面向 JP，固定 Japanese/ja；增加当前关键词工作簿表头别名；复用既有用户目录凭据，增加仅从标准输入更新和初始化 QA skip 的入口；CLI 二进制按 [backend-assets.json](backend-assets.json) 中版本与 SHA-256 按需下载到用户缓存，不提交二进制或凭据。写作预算沿用上游默认值，明确其为本技能配置，实际平台要求须在具体商品任务中核对。

`listing_config.py` 复用本仓库关键词技能的配置合并逻辑，使两个技能共享同一份客户端配置并保留 ABA 字段。技能目录重命名不改变历史凭据路径 `~/.amazon-jp-listing-asin/config.json`。

本地测试只能验证文件处理、结构、引用、隔离和报告行为，不能证明语言、商品事实或后端账号权限已经通过真实业务验收。
