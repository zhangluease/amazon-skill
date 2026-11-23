# Keepa 和变体曲线接口

卖家精灵扩展 5.0.7 使用以下只读接口，扩展请求入口为 `https://e.sellersprite.com/v2/extension`。下面的参数来自本机扩展程序，线上访问结果应独立记录。按日数据用 `DAY`，按小时用 `HOUR`。近七天是界面的图表范围选项，扩展用 `range.week` 调整显示起点；脚本应按返回时间和值对齐后裁切目标区间。

|标签内容|请求|处理方式|
|---|---|---|
|Keepa 主趋势|`GET /keepa?station=JP&asin={ASIN}&period=DAY`|`times` 对齐价格、Deal、购物车价、标价、Prime、FBA／FBM、大小类 BSR、评分、评论数、卖家数、购买量和优惠券等 `keepa` 序列。先检查点数、时间和缺值，再导出。|
|Listing 修改标记|`GET /listing/JP/asin-change/{ASIN}`|`startTime`、`endTime` 是毫秒。标题用 `changeType` 1、4、5、7，亮点比较 `previousHighlight/currentHighlight` 文本；五点是另一个字段。可单独查修改时间，无须主曲线成功。|
|按日图的广告标记|`GET /listing/JP/ads-trend/{ASIN}`|同样传 `startTime`、`endTime`。返回 `adsCampaign`、`adsGroup`。分别记录返回数量，空数组只代表此区间接口未返回相关记录。|
|变体对比的子体月购买量|`GET /chart/amz-unit-trend/variations?station=JP&asin={ASIN}`|按变体 ASIN 读取 `trend[].date`、`trend[].unit`。正整数按 `50+`、`100+` 等阈值展示，`null` 留空。这个月度序列与页面当前的「过去一个月 X+」使用不同时间窗口。|

卖家精灵的 [Keepa 官方 API](https://open.sellersprite.com/api/22) 可用开放平台授权查询 `GET https://api.sellersprite.com/v1/keepa/{marketplace}/{asin}`，支持区间参数和 `dailyLatest`，用 `dataAsin` 核对实际数据归属。官方 [MCP `keepa.trend`](https://cn.sellersprite.com/v3/knowledge/feature/mcp-interface-description) 同样提供逐日价格、购物车和排名等指标。这两个接口适合补历史趋势，公开文档未列出插件的 Listing 修改标记。历史 `listPrice`／`priceList` 和当前 Amazon 购买报价分别保存。

## 接口探查命令

仅需要「修改标题」「修改亮点」的时间，使用已接入扫码恢复的正式命令，无需 MCP：

```sh
node "SKILL/scripts/competitor.cjs" changes B0CDCD694T --out "/absolute/path/changes-7d"
node "SKILL/scripts/competitor.cjs" changes B0CDCD694T --all-history --out "/absolute/path/changes-all"
```

`--all-history` 请求接口可返回的全部周期，正常默认近七个日本自然日期。时间识别与实测结果见 [修改枚举和时间](data-contract.md#修改枚举和时间)。以下探查命令仅在需要其他趋势数据时使用。

```sh
python3 "SKILL/scripts/keepa_probe.py" B008H0V15I \
  --session-file "/absolute/path/http-session.json" \
  --out "/absolute/path/keepa-probe"
```

沿用 [已有专用会话](access.md#会话和浏览器)。用 `--as-of` 固定七天窗口的结束日。已确认主趋势拒绝时可加 `--skip-chart`，只查已知独立来源。探查输出 `keepa-api-probe.json` 和 `variation-monthly-purchases.csv`，主趋势取得时目前只导出数据点数量、时间范围和序列名，尚未接入完整趋势明细。

## 实测结果

2026-11-23 重新授权后，B008H0V15I 的商品身份核验成功。日本时间 11 月 17 日至 23 日的 Listing 修改、广告活动、广告组均返回有效空列表。变体曲线返回 1 个 ASIN、26 个按月点。扩展主趋势 `period=DAY` 在重新授权前后均返回 `ERR_GLOBAL_403`，独立价格趋势也返回同一错误。现有扩展会话的主趋势访问仍待确认。当前仓库没有已连接的卖家精灵开放平台 Keepa 工具，官方 REST／MCP 路径根据公开文档核对，尚未做授权实测。
