# 竞品查询

默认入口为 `scripts/sellersprite_browser.cjs search`，同时完成Amazon搜索、销量查询和图集生成。

## 候选与排序

1. 匿名HTTP读取Amazon.com搜索页HTML，提取主商品卡片ASIN，保存 `amazon-search.json`。
2. 向卖家精灵 `/v2/extension/competitor-lookup/US` 提交该ASIN清单，取得父体近30天销量和主图。
3. 限定在请求清单内排序，输出全部有效候选。接口额外返回的ASIN排除；缺失记录保留在 `ranked-products.json` 的 `incomplete`。

默认第一页，包含实际返回的主卡片广告。用户要求扩大范围时使用 `--amazon-pages 2`，最多4页、200个去重ASIN。匿名配送上下文和实时排序可能与用户已有浏览器不同；排名范围仅限取得的页面。

销量字段使用 `units`，主图使用 `image`，不能用子体销量、BSR或评论数代替。按ASIN去重，同父体不同ASIN保留；同销量按Amazon卡片顺序。图集仅显示编号、图片、ASIN、父体销量、商品链接和查询来源/时间。

## 授权与失败

正常查询复用本机卖家精灵HTTP会话。缺失或明确失效时，入口自动恢复授权一次，沿用已取得的ASIN文件继续请求；不重复搜索Amazon。人工登录步骤见 [环境与人工接管](environment.md)。

Amazon验证码、错误页或无法确认的搜索结果立即停止，不开浏览器重试、不用卖家精灵关键词库替换候选。只有明确无结果证据才返回empty，并跳过卖家精灵查询。限流、网络和其他业务错误按状态报告。

## 结果检查

`amazon-search.json` 记录候选来源，`request-plan.json` 记录ASIN参数，`sellersprite-candidates.json` 保留公开商品字段，`ranked-products.json` 保存排序和缺失项，`products.html` 用于多选。

失败时查看 `browser-status.json` 的category/stage，以及 `api-status.json`、`authorization.json`。环境检查通过不能代替实际取数成功；未取得有效销量和可查看主图时说明缺口。
