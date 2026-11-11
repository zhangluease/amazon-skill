# amazon-skill

面向日本亚马逊运营的轻量技能库。

## 日本站关键词查找

[amazon-jp-listing-asin](skills/amazon-jp-listing-asin/SKILL.md) 支持两种找词方式。

- **根据 ASIN 找词**。用竞品 ASIN 或 `amazon.co.jp` 商品链接，通过老陈的易逊后端反查，并用 ABA 全站搜索词中命中竞品点击 ASIN 的记录补充候选。
- **以词找词**。用日文种子词或配件型号查 Amazon 日本站搜索框联想、厂家官网和 ABA 全站搜索词，三个渠道都尝试，再合并去重。查配件时核对在售、停售和停产机型。

官网原词和官网组合词在采集后按目标商品及购买意图洗词：泛功能描述、与整机目标不符的零部件词及缺少型号依据的词不直接进入主表，完整候选、结论和原因保存在官网明细。Amazon 搜索框联想（关联词）不洗词，易逊与 ABA 候选也按原有规则保留。多来源同词不会因官网筛选被误删，详见[官网词相关性筛选](skills/amazon-jp-listing-asin/references/keyword-cleaning.md)。

将整个 `skills/amazon-jp-listing-asin/` 文件夹放入所用 Agent 的技能目录。脚本在首次使用时按需准备依赖。易逊 `backend_token` 与 ABA MCP 客户端 `aba_mcp.access_token` 统一保存在 `~/.amazon-jp-listing-asin/config.json`；兼容其他 Agent 已写入的 `spapi_mcp.access_token`。用 `environment.py configure` 从标准输入合并更新，用 `environment.py config-status` 查看脱敏状态，`environment.py normalize-config` 可归一旧字段。

ABA 默认通过随附 `aba_client.py` CLI 直连服务，无需 Agent 注册 MCP，也无需安装 MCP SDK；只需 Python 3.9+。运行 `python3 skills/amazon-jp-listing-asin/scripts/aba_client.py status` 实测连接，`call` 执行工具，`collect-query` 完整采集并支持分页中断续取。选择原生 MCP 时也可通过 `aba_mcp_headers.py` 读取同一配置，详见[客户端配置](skills/amazon-jp-listing-asin/references/client-config.md)。

ABA 使用已连接的 `https://spapi-mcp.bamboost.info/mcp`，无需自有 ASIN；连接不可用或报表未完成时说明状态，继续交付已有候选。ABA 排名和份额分别标注，不当作月搜索量或易逊流量占比。报表与筛选任务保存 ID，可续查并复用压缩源缓存。

ABA 默认目标日期为查询日前 7 天，不含查询当天。例如 11 月 11 日查询对应 11 月 4–10 日，拆成 7 份 `DAY` 报表。`aba_client.py plan-window` 可生成逐日报表参数；默认查询日按北京时间，也可用 `--as-of YYYY-MM-DD` 指定。关键词合并去重，指标保留逐日明细。未取得的日期明确标注缺口，不用零补齐，不把每日指标相加或平均成 7 日指标。用户指定完整周、月或季度时仍支持相应单周期。

```text
请使用 $amazon-jp-listing-asin 反查我提供的日本亚马逊竞品 ASIN，整理前 20 个日文流量词和月搜索量。
请使用 $amazon-jp-listing-asin 以「水筒」和「ステンレスボトル」找词，合并 Amazon 日本站搜索框联想和厂家官网结果，官网词洗词、Amazon 联想词全部保留，交付主表和完整来源明细。
请使用 $amazon-jp-listing-asin 以 H060518 加湿器滤芯找词。查询 Amazon 日本站联想和大日官网，核对在售、停售及停产机型，用型号和官方部件名扩词，去重后附官方证据。
```

两种方式都交付 Excel。ASIN 反查交付 `keywords.xlsx`，以词找词交付 `seed-keywords.xlsx`；官网完整候选及洗词结论另存 `official-keywords.xlsx`，已取得的 ABA 商品与周期明细另存 `aba-keywords.xlsx`。报告说明官网洗前、保留、剔除及待核实数量。同一任务执行两种方式时，两份主表都交付。单独运行以下联想脚本可查看日文原词和联想顺序。

```sh
python3 skills/amazon-jp-listing-asin/scripts/amazon_jp_autocomplete.py \
  --output reports/jp-keywords/example/autocomplete.xlsx "水筒"
```

## 日本站知识产权初筛

[amazon-jp-ipr-screening](skills/amazon-jp-ipr-screening/SKILL.md) 用于选品或上架前，检查单个商品在日本的特许、实用新案和意匠风险。默认中文交付，保留日语检索词和原始权利名称。

流程：商品资料 → J-PlatPat 接口检索 → 候选核对 → 风险与证据报告。

### 使用

把整个 `skills/amazon-jp-ipr-screening/` 文件夹放入所用代理的技能目录。

```text
请使用 $amazon-jp-ipr-screening，检查这个商品在日本亚马逊的特许、实用新案和意匠风险：

商品：提供 amazon.co.jp 链接，或商品图片、名称、用途及结构说明
销售方式：自有品牌 / 通用品牌 / 经销 / 兼容配件
已知线索：竞品、权利号、投诉记录或授权文件（有则提供）
```

可以先给商品链接和已有资料；缺失信息会按影响标注，不必先填完表格。只有竞品链接时，会先区分参考商品与准备销售的商品。

默认报告保存到 `reports/<日期>-<商品简称>/report.md`，证据放在同目录 `evidence/`。报告包含已查明风险、待定项、检索记录和下一步行动。
