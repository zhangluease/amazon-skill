# 数据约定

## 已核对的接口

服务根地址为 `https://e.sellersprite.com`。以下都是 GET，仅查询用户输入的日本站 ASIN。

|用途|路径|业务参数|
|---|---|---|
|商品快照|`/v2/extension/asin/JP`|`asins=ASIN`|
|竞品销量|`/v2/extension/competitor-lookup/JP`|`q=`、`asins=ASIN`、`tk=扩展校验值`|
|修改标记|`/v2/extension/listing/JP/asin-change/ASIN`|`startTime`、`endTime`（Unix 毫秒）|

Amazon 当前报价使用另一个匿名 HTTP 会话，固定 `https://www.amazon.co.jp`。先 GET 商品并核对配送：`COUNTRY` 标签明确为日本（如“日本”或“Japan”），或同一会话此前确认的日本目的地仍匹配时，直接读取报价，不调用地址接口或额外刷新。证据中 `selection_method=existing_session` 表示初始国家已为日本。

国家非日本、信息不明或已改变时，再 GET 控件声明的地址弹层取得 CSRF → POST `/portal-migration/hz/glow/address-change?actionSource=glow`，JSON 为 `locationType=COUNTRY,countryCode=JP,district=JP`，不提供邮编 → 刷新原商品并核对顶部配送标签。切换结果必须 `sembuUpdated=1` 且返回国家为 JP；若服务器解析为日本邮编，则对照刷新页核验该邮编。此时 `selection_method=country_change_api`。不把国家请求擅自改为固定东京邮编，也不只凭邮编形式推断初始国家。逻辑源自 POD 选图的配送设置流程，并适配日本站。

`amazon_product.py` 使用标准库解析 HTTP 返回正文，不启动浏览器。日本配送未确认前的价格、折扣、参考价及购物车／库存不会进入当前报价字段。Amazon 与卖家精灵会话隔离，CSRF 和 Cookie 不进入证据文件。验证页（包括 `/errors_page/validateCaptcha`）即使返回 HTTP 200 也会失败并停止该轮 Amazon 后续请求；不在登录恢复时重复请求验证页。

会话附带当前扩展的 `version`、`language`、`extension`、`source` 参数及私有认证头。`tk` 使用原扩展现行算法，不是账号凭据。接口固定到 JP，输入中的其他站点链接会被拒绝。

商品响应为 `code=OK, data=[商品对象]`，销量为 `code=OK, data={items:[商品对象], ...}`，修改记录为 `code=OK, data=[修改对象]`。返回其他 ASIN 不会错配到本轮商品，空商品列表记为 `ASIN_NOT_RETURNED`。异常业务响应及 null 历史不转换成空列表。仅返回 `crawlAsins`（包含目标 ASIN）表示来源没有缓存，需要额外取数，记为 `PRODUCT_NOT_CACHED`，不当成响应结构损坏。

本版直接请求修改记录接口，无需等待 Keepa 图表渲染或请求全量价格曲线。扩展图表的修改图标也来自这个修改接口；HTTP 200、图表加载中或价格曲线存在均不能替代修改记录成功。

## 字段口径

|结果|接口字段|说明|
|---|---|---|
|ASIN、父体、标题、品牌|`asin`、`parent`、`title`、`brand`|商品快照优先，缺失时用竞品接口|
|主图|`image_zoom`，缺失时 `image`|只允许 Amazon 图片域名；输出 URL 和报告缩略图，不进行 OCR|
|大类排名和类别|`bsr`、`bsr_label`|来源原值，不用小类排名替代|
|小类排名|`bsr_list`／`bsrList` 中 `main=false` 的 `rank,label`|不按数组第一项猜大小类；缺少标记的行不强行分类|
|父体近30天销量|销量接口 `units`|卖家精灵估算值；缺失留空，真实 0 保留|
|子体近期购买量|`month_units`|插件以 `useNumber(value,0)+"+"` 展示；数值 100 转为 `100+`，1000 转为 `1,000+`。已有加号保持原样；JSON 另存原始数值，不当作精确子体销量|
|价格、币种|已确认日本配送的 Amazon `priceToPay`／传统报价控件；缺失时保留供应商 `price`|逐字段注明当前 HTTP 来源或 API 快照；`円` 统一为 JPY|
|评分、评论数|`rating`、`reviews`|数值类型检查，不用缺失补 0|
|当前参考价|日本配送已验证时，当前价格区 `basisPrice` 的报价及标签|保留“参考価格／過去価格”等实际标签，不混入历史 Keepa 参考价；未显示或取数失败为未知|
|当前折扣|当前价格区 `savingsPercentage`|如 `-37%` 输出折扣 37；不从无依据参考价推算|
|购物车|明确可用的 `add-to-cart-button`／`add-to-cart-button-ubb`，或明确无推荐出品文本|没有控件本身不证明无购物车；禁用按钮记为无可用购物车|
|当前库存|`availability` 明确在库／缺货文本|保存文本，不把“无法配送”判断成缺货；不猜库存数量|
|近7天修改标题时间|修改接口 `changeType` 1、4、5、7 的 `timePoint`|完整保留时间字符串，去重排序；空列表为未观测到，null 为未知|
|近7天修改亮点时间|`previousHighlight/currentHighlight` 文本不同的 `timePoint`|null 转空串、数组各项转文本后按换行连接，与扩展 5.0.7 保持一致；不使用五点枚举替代|

`unavailable=false` 可能是扩展默认值，`unavailable=true` 也只是来源快照。可在证据中保留，不能当作当前库存事实。`seller_id`、报价、卖家数量也不证明当前 Buy Box。Amazon 当前报价仅在配送核验成功、返回 ASIN 与输入相符后合并；未核验的字段不会覆盖主表。

## 修改枚举和时间

`changeType` 在核对的 5.0.7 原扩展中定义如下，不是位掩码：

|值|含义|
|---|---|
|0|未变化|
|1|标题|
|2|图片|
|3|五点|
|4|标题＋图片|
|5|标题＋五点|
|6|图片＋五点|
|7|标题＋图片＋五点|

「修改亮点」独立于上述枚举，来自 `previousHighlight/currentHighlight`。扩展后台先将 null 转为空串、数组按 `\n` 连接，再由前台比较两个文本；五点 `previousFeatures/currentFeatures` 变化不会自动变成亮点变化。两个亮点字段都缺失时，本脚本保留未知，不把旧版漏存字段的缓存当作未变化；未知枚举时仍可识别字段明确的亮点变更。

修改记录的原始 `timePoint`、`changeType`、前后标题、亮点、图片和五点均在白名单内。未知枚举、无效日期或异常条目使来源状态变为 partial；仍保留有效条目。`title_change_times`、`highlight_change_times` 分别只包含已识别事件的原始时间，去重排序，同一天不同时间不会合并成一个日期。来源按日标记返回的 `00:00:00` 不代表实际修改发生在零点。图片和五点保留在 JSON 诊断证据中，正常表格只列标题、亮点时间；视频不作为所需字段。

默认观察结束时间为运行时日本时间，开始时间为结束日往前六天的 00:00，共七个自然日期。`--as-of YYYY-MM-DD` 以该日末尾为终点；带时间的 `--as-of` 必须含 UTC 偏移。请求使用这个区间的 Unix 毫秒，返回记录再按原始 `timePoint` 日期筛选；来源时间时区未声明，单独记录这个限制。

历史成功返回 `[]` 表示这个区间的来源列表未观测到相关修改，不能保证 Amazon 没有改过。接口没有完整覆盖声明，`provider_coverage=not_reported`；失败时两个时间字段均为 null。商品资料和销量都未确认目标 ASIN、修改接口又只有空列表时，商品身份未确认，历史状态为 `unconfirmed_product`、修改结果为未知，整条结果为失败。

只需标题与亮点时间时运行 `competitor.cjs changes`，默认七天；加入 `--all-history` 时请求 `startTime=0`、`endTime=查询结束毫秒`，保留所有接口可返回的记录。不会请求 Keepa 主曲线或 Amazon 页面，空修改列表时另查商品快照确认 ASIN。输出 `changes.json`、`changes.csv`、`report.html`；`evidence/ASIN-listing-changes.json` 保存白名单原始变更，故能复查亮点数组／null。鉴权失效沿用入口的一次扫码及同窗口续查，失败不会填无变化。

2026-11-23 实测 B0CDCD694T：全周期返回 6 条修改记录，`changeType=4` 对应 `2025-08-31 00:00:00` 的标题变更，其余 5 条为图片变更，均不进入时间表。6 条记录的前后亮点都是 null，因此未观测到亮点变更。11 月 17 日至 23 日的直接查询返回有效空列表，商品身份另经接口确认，近七天两类修改均未观测到。该案例验证了真实标题事件；亮点正例由数组、文本、添加和删除的离线样本验证，未声称本案例存在亮点正例。

## 本地交付

`products.json` 保存 `format`、站点、观察区间、原始请求 ASIN 顺序、停止原因和 `products`。每个商品包含字段、`field_sources`、`missing_fields`、`errors` 和修改 `history`。`products.csv` 是同一批商品的平铺列，保留未知列，防止以公式执行文本字段。`report.html` 对全部商品展示主图、字段及异常，没有外部脚本。

每个商品还包含 `delivery_destination`（请求国家、服务器解析的邮编、刷新页标签、`verified`）和 `delivery_status`。`evidence/ASIN-amazon.json` 保存配送验证、公开报价字段、证据文本及响应摘要；不保存含 CSRF 的原始 HTML。验证失败时保存失败类别、`verified=false`，其他来源成功的数据继续交付。

浏览器补采使用 `https://www.amazon.co.jp/dp/{ASIN}` 识别商品。页面明确显示配送至日本，或商店显示日本且顶部和购买区的配送邮编一致时，才合并页面报价。`evidence/ASIN-amazon-browser.json` 保存报价原文、邮编和观察时间。报告使用同一页面观察的报价、折扣和购买状态，库存记录为在库或缺货状态。

`evidence/ASIN-product.json`、`ASIN-sales.json`、`ASIN-changes.json` 只保存各来源允许的业务字段、端点和实际取得时间。修改证据还保存本轮观察区间。重新授权后只复用同一任务、同一 ASIN 清单、同一历史区间的成功证据；失败的来源再次查询。商品快照时间和历史观察区间分开，指定历史结束日不代表取得该日的商品快照。

缺失字段仍是 null。完整商品采集在所需字段齐全、历史接口正常且无来源错误时为 `ok`，有缺口或来源异常为 `partial`；商品身份未确认且没有有效资料或历史事件时为 `failed`。只查修改时间的模式在接口正常且身份可确认时为 `ok`，异常为 `partial`／`failed`。成功导出不代表来源覆盖完整。`browser_fallback_asins` 列出当前报价待补采的商品。日本配送与当前报价的实测状态以当轮证据为准，离线成功不等于当轮配送已验证。
