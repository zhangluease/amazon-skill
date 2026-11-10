# 厂家官网适配型号查询

以日本市场的配件名称、部件号或厂家链接为入口。优先确定厂家、部件号及配件版本。只有名称时，先用官方资料定位部件号。发现多个可能对应的部件时，按部件号分别核对，标明需要用户确认的版本。

## 查找和核对

1. 从厂家官网的配件页、官方商城、消耗品适配表或按机型查部件的工具入手。用准确部件号搭配 `適用機種`、`適応機種`、`対応機種`、`消耗部品` 或 `別売部品` 检索。打开原页核对对应关系。
2. 默认分别检查在售产品目录和历史产品入口，即使配件页已经列出适用机型。沿 `生産終了`、`販売終了`、`旧製品`、`過去の製品` 等入口查各系列和年份。未找到统一的旧款入口，或适配表覆盖不清楚时，查官方说明书、历年目录及旧款产品页，从整机的消耗品表反查部件号。检查表格分页、折叠内容、下载文件和表注。记录已覆盖的系列、年份和无法核对的入口。
3. 只有官方正文、表格或说明书明确对应同一部件号与整机型号时，才将该型号计入已确认适配。按表头、行列和脚注核对，留意颜色、渠道、年份及替代件限制。型号相近、同系列、第三方卖家描述或搜索摘要只作线索。现行配件表未列出的旧机型可由旧版官方资料确认。官方资料若对同一版本明确给出相反结论，保留两边证据，标为待确认。
4. 按「部件号 × 完整整机型号」去重，合并同一配对的来源。只在表格上下文明确时补全共享前缀，保留官方写法与有意义的后缀，避免合并不同型号。机型状态按官方标注记录为在售、停售、停产或未确认。旧年份或未出现在当前目录，只能作为查找旧款资料的线索。

## 用型号和官方部件名组合扩词

1. 从官网配件表的「部品名」或配件详情页提取目标部件的日文名称。按同一列的「商品コード」及该表适用的整机型号核对。表格换行导致一个部件名分成多行时，将其还原为完整名称，保留区分版本所需的词和后缀。
2. 将官方部件名单独纳入候选，并分别生成 `部件号 + 空格 + 官方部件名` 和 `已确认适配的整机型号 + 空格 + 官方部件名`。每个整机型号单独生成一条。部件名称有多个官方写法时，只组合证据支持的型号和名称，并保留对应来源。
3. 将组合词标为「官网组合」，记录部件名、前缀型号、官网 URL 和证据位置。同一组合恰好也出现在 Amazon 联想时，合并为一行并同时保留联想位置和官网依据。

## 候选词的版本和属性标注

- 同一整机可适配多种配件。联想含其他部件号、版本或 `純正`、`互換品`、`使い捨て` 等属性时，原词和来源仍进入完整候选表。已有证据可在事实备注写明「版本不同」「属性待核实」等。官网确认的原厂适配关系单独记录。
- 例如，查 `H060518` 时，联想词 `h060518 フィルター 使い捨て` 仍进入 `seed-keywords.xlsx`。有官网证据时可备注「一次性版本与目标部件版本不同」，交给后续洗词处理。

## 日本加湿器品牌官方查询入口

按已确认的品牌选用下表。只知道部件号时，先查部件号确认品牌。遇到表外品牌或链接变动时，沿该品牌的日本官网查同类入口。

| 品牌 | 配件或产品入口 | 旧款核对入口 |
| --- | --- | --- |
| 大日（ダイニチ） | [官方商城加湿器配件页](https://webshop.dainichi-net.co.jp/c/814186/814187)可查部件号或整机型号，[当年机型对比](https://www.dainichi-net.co.jp/products/humidifier/comparison/)核对当前产品 | [历史机型对比](https://www.dainichi-net.co.jp/support/spec/humidifier/)和[历年加湿器说明书](https://www.dainichi-net.co.jp/support/download/manual/humidifier/) |
| 夏普（シャープ） | [加湿器配件查询](https://jp.sharp/support/humid_con/option.html)按整机型号查配件；加湿空气净化器走[对应配件页](https://jp.sharp/support/air_purifier/doc/filter_top.html?qr=m) | [加湿器说明书](https://jp.sharp/support/humid_con/download.html)按整机型号或型号前缀查找 |
| 松下（パナソニック） | [加湿机消耗品目录](https://panasonic.jp/consumables/products.html?category_uid=NTkyMA%3D%3D&consumables_category_flag=true)按部件号或整机型号筛选 | [加湿机支持页](https://panasonic.jp/kashitsu/support.html)查说明书和产品资料，停产状态以对应整机页标注为准 |
| 三菱重工冷热系统（三菱重工サーマルシステムズ） | [加湿器另售配件](https://www.mhi-mth.co.jp/support/products/customer/option/humidifier/)含蒸发布与整机型号对应表 | [历年蒸发布适配表](https://www.mhi-mth.co.jp/support/products/customer/option/humidifier-filter/)补查旧款，[加湿器说明书](https://www.mhi-mth.co.jp/manual/index.php?action=manual&category=hc_humidifier)分蒸汽式和混合式查询 |
| 象印（象印マホービン） | [官方部件商城的加湿器分类](https://www.zojirushi-parts-direct.com/category/D004/)按整机型号进入配件清单 | [加湿器说明书](https://www.zojirushi.co.jp/toiawase/manual/consumer-electronics/index.php)在生活家电中筛选「加湿器」 |
| 爱丽思欧雅玛（アイリスオーヤマ） | [加湿器产品目录](https://www.irisohyama.co.jp/products/season/humidifier/)逐机型查规格中的「別売品」 | [加湿器说明书索引](https://www.irisohyama.co.jp/products/manual/20)按型号搜索或逐页查找 |
| 山善（YAMAZEN） | [加湿器说明书索引](https://book.yamazen.co.jp/support/manual_search/?category=5&subcategory=40)可筛选「加湿器フィルター」 | 同一索引包含部分停产机型，可打开整机说明书查「消耗品」 |

## 交付字段

- `official-models.xlsx` 逐行保留部件号、整机型号关键词、适配核对结果、机型状态、官方证据 URL 和位置（页面章节、表格、PDF 页码或对应条目）。机型状态若由另一页面确认，附上该页链接。待确认项单独标记和计数。型号存为文本，月搜索量和流量占比留空。
- `official-keywords.xlsx` 保留官网部件名和组合词，逐行标注「官网原词」或「官网组合」，并附上型号与名称对应的证据。官网候选和联想候选合并去重后进入 `seed-keywords.xlsx`。
- 报告列明查过的在售和历史来源、已确认数量、状态分布及未覆盖范围。只有官方资料足以证明对应范围已覆盖时才写「全部」；其余情况用「已核实的适配型号」并列明缺口。页面无法访问、资料覆盖不足与官方明确无适配分别记录。

## 示例入口

以大日（ダイニチ）加湿器滤芯 `H060518` 为例，先读 [官方商城的适用机型表](https://webshop.dainichi-net.co.jp/c/814186/814187/814198/filter-h060518)，再分别查 [当年机型对比页](https://www.dainichi-net.co.jp/products/humidifier/comparison/) 和独立的 [历史机型对比页](https://www.dainichi-net.co.jp/support/spec/humidifier/)。可从 [已停产的 2024 年 N 系列产品页](https://www.dainichi-net.co.jp/products/humidifier/lineup/n2024/) 的「別売部品」表核对配件号，也可沿 [按年份整理的加湿器说明书索引](https://www.dainichi-net.co.jp/support/download/manual/humidifier/) 回查更早的机型。

大日的 2024 年 RXT・RXC 系列配件表将 `H060518` 对应到 `抗菌気化フィルター`，表头列出 `HD-RXT524` 和 `HD-RXC500C`。这张表可用于生成 `H060518 抗菌気化フィルター`、`HD-RXT524 抗菌気化フィルター` 和 `HD-RXC500C 抗菌気化フィルター`。相邻列的 `カンタン取替えフィルター` 对应 `H060524`，应按其部件号单独处理。[大日 RXT・RXC 产品页「別売部品」](https://www.dainichi-net.co.jp/products/humidifier/lineup/rxt2024/)
