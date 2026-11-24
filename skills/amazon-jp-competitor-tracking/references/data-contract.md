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

鉴权异常或关键快照字段缺失时，额外调用固定官网端点 `GET https://www.sellersprite.com/v2/extension/tk/signin`，用已有令牌续登后重试当前请求一次。续登响应仅用于私有凭证更新，不进入业务证据；空值和状态按 [API 续登规则](access.md#api-续登和空数据) 处理。`ERR_ROBOT_CHECK` 对应 `verification_required`，不能当成普通空数据或失败后写“否”。

商品响应为 `code=OK, data=[商品对象]`，销量为 `code=OK, data={items:[商品对象], ...}`，修改记录为 `code=OK, data=[修改对象]`。返回其他 ASIN 不会错配到本轮商品，空商品列表记为 `ASIN_NOT_RETURNED`。异常业务响应及 null 历史不转换成空列表。仅返回 `crawlAsins`（包含目标 ASIN）表示来源没有缓存，需要额外取数，记为 `PRODUCT_NOT_CACHED`，不当成响应结构损坏。

目标商品的业务字段为 `****` 等纯星号时表示未登录遮罩，分类为 `PLUGIN_LOGIN_REQUIRED`，即使 HTTP 200 且业务 code 为 OK 也不能视为成功取数。先尝试 API 续登，再检查重试结果；仍被遮罩时进入一次浏览器授权。对外字段不保留星号值，未解锁的图库结果为未知，不能写成“否”。

本版直接请求修改记录接口，无需等待 Keepa 图表渲染或请求全量价格曲线。扩展图表的修改图标也来自这个修改接口；HTTP 200、图表加载中或价格曲线存在均不能替代修改记录成功。

## 字段口径

|结果|接口字段|说明|
|---|---|---|
|ASIN、父体、标题、品牌|`asin`、`parent`、`title`、`brand`|商品快照优先，缺失时用竞品接口|
|主图|Amazon `landingImage`，缺失时用同 ASIN 的 `image_zoom` 或 `image`|只允许 Amazon 图片域名。报告输出 URL 和缩略图|
|数量装|当前标题的明确件数、商品正文规格、主图识别|按顺序使用。正文规格优先商品件数，其次包装数量。主图结果绑定 ASIN 和图片 URL，来源与观察说明单独保存|
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
|近7天图片/视频变化|修改接口 `changeType` 2、4、6、7|与插件合并图库图标一致；true 写“是”、成功未观测到写“否”、失败为未知|

`unavailable=false` 可能是扩展默认值，`unavailable=true` 也只是来源快照。可在证据中保留，不能当作当前库存事实。`seller_id`、报价、卖家数量也不证明当前 Buy Box。Amazon 当前报价仅在配送核验成功、返回 ASIN 与输入相符后合并；未核验的字段不会覆盖主表。

## 图库修改口径

只处理卖家精灵 Keepa 的“修改图片/视频”合并图标。已核对扩展 5.0.7：`changeType` 2、4、6、7 是图库事件，不使用位运算。其他合法枚举不计入；未知枚举或无效日期导致 partial。保留 `timePoint`、`changeType`、前后图片字段，其他事件和字段丢弃，不另行采集其他类型的修改记录。

`media_change_observed` 为 true 表示近七天至少一个有效图库事件，false 表示接口成功且未观测到相关事件，null 表示无法确认。接口失败不变成 false；商品身份未确认时空列表也是未知。该字段对应 UI 的合并图标，不拆分成两个独立判定。来源完整覆盖未声明，`provider_coverage=not_reported`。

默认查询结束日往前六天 00:00 至运行时，共七个日本自然日期。`--as-of YYYY-MM-DD` 以当日末尾为终点，带时间需含 UTC 偏移。请求用 Unix 毫秒，返回记录再按来源日期筛选；来源时区未声明。`--all-history` 只用于诊断接口可返回的全部周期，飞书回写必须使用七天窗口。

`competitor.cjs changes` 直接请求修改接口，不需要 Keepa 主曲线或 MCP。输出 `changes.json`、`changes.csv`、`report.html`；证据只保留图库相关字段。schema 为 `listing-media-changes-v3`，旧版成功缓存不能作为新口径证据。独立授权恢复沿用同一窗口。

## 本地交付

`products.json` 保存 `format`、站点、观察区间、原始请求 ASIN 顺序、停止原因和 `products`。每个商品包含字段、`field_sources`、`missing_fields`、`errors` 和修改 `history`。`products.csv` 是同一批商品的平铺列，保留未知列，防止以公式执行文本字段。`report.html` 对全部商品展示主图、字段及异常，没有外部脚本。

每个商品还包含 `delivery_destination`（请求国家、服务器解析的邮编、刷新页标签、`verified`）和 `delivery_status`。`evidence/ASIN-amazon.json` 保存配送验证、公开报价字段、证据文本及响应摘要；不保存含 CSRF 的原始 HTML。验证失败时保存失败类别、`verified=false`，其他来源成功的数据继续交付。

浏览器补采使用 `https://www.amazon.co.jp/dp/{ASIN}` 识别商品。页面明确显示配送至日本，或商店显示日本且顶部和购买区的配送邮编一致时，才合并页面报价。`evidence/ASIN-amazon-browser.json` 保存报价原文、邮编和观察时间。报告使用同一页面观察的报价、折扣和购买状态，库存记录为在库或缺货状态。

`evidence/ASIN-product.json`、`ASIN-sales.json`、`ASIN-changes.json` 只保存各来源允许的业务字段、端点和实际取得时间。修改证据还保存本轮观察区间。重新授权后只复用同一任务、同一 ASIN 清单、同一历史区间的成功证据；失败的来源再次查询。商品快照时间和历史观察区间分开，指定历史结束日不代表取得该日的商品快照。

缺失字段仍是 null。完整商品采集在所需字段齐全、历史接口正常且无来源错误时为 `ok`，有缺口或来源异常为 `partial`；商品身份未确认且没有有效资料或历史事件时为 `failed`。只查图库变化的模式在接口正常且身份可确认时为 `ok`，异常为 `partial`／`failed`。成功导出不代表来源覆盖完整。`browser_fallback_asins` 列出当前报价仍有缺项的商品，参考价或折扣未展示不代表应重试。`amazon_browser_verification_asins` 标记访问确认或限流暂停涉及的商品。飞书写回约定见 [飞书 CLI 工作流](feishu.md)。日本配送与当前报价的实测状态以当轮证据为准，离线成功不等于当轮配送已验证。

Amazon 自动无头补采与人工页面观察都使用 `amazon_jp_browser_offer` 字段来源，快照的 `collection_method` 区分 `browser_headless`、`browser_headed` 和 `browser_page`。自动补采复用 `amazon_product.py` 的身份、配送和报价解析，返回公开字段及正文摘要哈希，不保存完整 HTML 或页面令牌。HTTP 确认页与验证码输入页分别记录 `AMAZON_CONTINUE_SHOPPING_REQUIRED` 和 `AMAZON_HUMAN_VERIFICATION_REQUIRED`；两个状态都不能当成有效商品响应。
