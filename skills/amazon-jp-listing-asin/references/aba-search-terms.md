# ABA 全站搜索词补充

用于 ASIN 反查和以词找词。MCP 地址为 `https://spapi-mcp.bamboost.info/mcp`，固定日本站 `A1VC38T7YXB528`。使用已连接的工具和服务器端授权，不在技能、参数或报告中保存凭据。默认只使用 `GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT`；无需自有 ASIN，不调用需要自有品牌 ASIN 的 SQP。

## 创建与续查

1. 发现并调用 `aba_status`，检查凭据与 `searchTermsQuery.supported`。工具缺失、未连接、凭据未配置或旧版未提供筛选工具时，记录具体状态，继续其他渠道。不要为取得 ABA 而要求用户提供自有 ASIN。
2. 默认查询最近一个已结束的完整周（星期日至星期六），以日本站日期计算。用户指定日、周、月或季度时遵从指定周期；不能跨多个周期，也不能使用未结束的周期。保存统计日期与本次查询时间及其时区。
3. 在报告根目录已有记录中，按站点、报表类型、周期和日期查找可复用的 `reportId`。已提交的报表用 `aba_get_report` 续查，不重复创建。首次创建时调用：

   ```json
   {
     "reportType": "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT",
     "reportPeriod": "WEEK",
     "dataStartTime": "2026-11-01T00:00:00Z",
     "dataEndTime": "2026-11-07T23:59:59Z"
   }
   ```

   日期仅为调用格式示例，执行时替换。创建这个报表时不传 `asins`；竞品筛选在后续工具中进行。立即把请求、返回的 `reportId` 和状态写入 `RUN/aba-manifest.json`，同一任务的两种模式共用报表。
4. 按至少 30 秒间隔查状态，遇限流按服务端提示退避。`IN_QUEUE`、`IN_PROGRESS` 记录为处理中。`FATAL`、`CANCELLED` 或请求失败记录真实状态，不能当作无匹配。只有 `DONE` 才提交筛选。默认本轮最多等待 15 分钟；仍在处理时保存 ID，交付其他渠道已取得的结果并标注 ABA 待完成，后续可续查。用户明确要求等待完成时继续等待。

## 服务端筛选与分页

- **ASIN 反查**：调用 `aba_query_search_terms`，传 `reportId` 和竞品 `asins`，精确匹配 `clickedAsin`。每批最多 20 个；不要求这些 ASIN 属于当前账号。
- **以词找词**：传 `reportId` 和 `seedTerms`。使用输入的日文种子词、部件号及已确认的官方部件名或型号写法；每批最多 20 个。匹配按全半角与字母大小写归一后的字面包含进行，结果保留原词。记录实际使用的筛选词。仅凭命中不能确认配件适配，也不保证召回语义近似词。
- 两种参数同时传入时取并集。分别保留 `matchedAsins` 和 `matchedSeedTerms`，避免把种子词命中误标为竞品 ASIN 命中。不同批次复用同一压缩源缓存。
- 保存返回的 `queryId` 和筛选条件到 manifest。按 `pollAfterSeconds` 调用 `aba_get_search_terms_query`。后台筛选与亚马逊报表生成是两种不同的处理中状态。服务忙时等待后续查已有任务，不反复创建报表。重启中断的任务明确标为 `FAILED/QUERY_INTERRUPTED`，可重新提交原筛选，复用已下载的源文件。
- 完成后沿 `nextCursor` 读取全部页面，默认每页 50 条。每个查询从 `cursor=null` 的首页保存，保留每页返回的 `cursor`、`nextCursor`、`status`、`coverageComplete`、计数和全部 `rows`。不要先截取前 20 个词；20 词限制只适用于报告摘要。
- 只有 `DONE`、`coverageComplete=true` 且分页完整，才能标为完整采集；此时零行才是成功无匹配。`PARTIAL` 表示达到命中上限，保留已返回的词并说明覆盖缺口。`FAILED`、扫描上限、存储上限、网络失败和分页未完成都不能写成无匹配。已取得的有效页面可按部分结果交付。
- 大报表使用这两个筛选工具，不走 `aba_download_report` 全量解压再读取。后者仍有 512 MiB 的解压大小限制。筛选任务缓存最多 256 MiB 的加密压缩源，逐条扫描最多 8 GiB 解压内容，最多保留 200,000 条商品记录；超限须说明状态。

## 保存与导出

把工具正文解析为 JSON，按下面的容器格式保存到 `RUN/aba-query-pages.json`。成功或部分取得结果时，`pages` 只放同一完成状态的结果页面；尚在处理或失败时，只放最新的一次状态对象，以便生成带表头的空明细并保留状态。不同批次分别放在 `queries` 中。实际请求与轮询记录保存在 manifest。

```json
{
  "queries": [
    {
      "reportPeriod": "WEEK",
      "pages": ["替换为从首页到末页的完整结果对象"]
    }
  ]
}
```

执行随附转换器校验分页、保留明细并生成通用表数据：

```sh
python3 "SKILL/scripts/aba_to_table.py" \
  --input "RUN/aba-query-pages.json" --output "RUN/aba-keywords.json"
python3 "SKILL/scripts/export_xlsx.py" \
  --input "RUN/aba-keywords.json" --output "RUN/aba-keywords.xlsx"
```

转换器的 `query_statuses` 区分完整、部分、处理中与失败；检查它和终端摘要。`rowsMatched`、`rowsScanned` 是商品记录数，不是去重关键词数。`aba-keywords.xlsx` 保留按「报表＋部门＋原词＋ASIN」区分的明细；任务主表按原有去重规则合并词并汇总全部来源，记录 ABA 明细的位置。同词跨部门、跨周期或对应多个 ASIN 时保留各自指标，不取最小排名冒充统一排名，也不把份额相加。

## 指标解释与合并边界

| 字段 | Excel 列 | 口径 |
| --- | --- | --- |
| `searchTerm` | 日文关键词 | 原始买家搜索词 |
| `departmentName` | ABA搜索部门 | 搜索范围，排名比较须保留 |
| `searchFrequencyRank` | ABA搜索频率排名 | 同周期、同部门的频率排名，越小越热门；不是搜索量 |
| `clickedAsin` | ABA点击ASIN | 该词下热门点击商品，不推断商品所有权 |
| `clickShareRank` | ABA点击份额排名 | 商品点击份额排名，不是自然搜索位置 |
| `clickShare` | ABA点击份额 | 该商品点击数／这个词的全部点击数 |
| `conversionShare` | ABA转化份额 | 该商品转化数／这个词的全部转化数，不是转化率 |

份额为 0–1 的比例，Excel 用百分比格式，零保持为零，缺失留空。ABA 不提供绝对搜索量，不能从排名估算月搜索量；ABA 份额不能填进易逊的流量占比列。主表已有易逊指标时保留原值和来源。ABA-only 候选的月搜索量和易逊流量占比留空。

全站搜索词报表仅覆盖各词的热门点击商品，通常为前三个商品。竞品未命中只表示未在该期覆盖范围中命中。联想、官网或易逊候选不因 ABA 未命中而删除。品牌词、型号差异与待确认关系按现有规则保留并备注。

字段依据：[亚马逊报表说明](https://developer-docs.amazon/sp-api/docs/report-type-values-analytics)、[官方搜索词 Schema](https://github.com/amzn/selling-partner-api-models/blob/main/schemas/reports/sellingPartnerSearchTermsReport.json)。
