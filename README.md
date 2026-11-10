# amazon-skill

面向日本亚马逊运营的轻量技能库。

## 日本站关键词查找

[amazon-jp-listing-asin](skills/amazon-jp-listing-asin/SKILL.md) 支持两种输入。竞品 ASIN 或 `amazon.co.jp` 商品链接通过老陈的易逊后端反查流量词。日文种子词通过 Python 脚本请求 Amazon 日本站搜索框联想。提供本品资料时，可按相关性和搜索意图筛词。

将整个 `skills/amazon-jp-listing-asin/` 文件夹放入所用 Agent 的技能目录。脚本在首次使用时按需准备依赖。ASIN 反查使用本机配置的易逊 token。

```text
请使用 $amazon-jp-listing-asin 反查我提供的日本亚马逊竞品 ASIN，整理前 20 个日文流量词和月搜索量。
请使用 $amazon-jp-listing-asin 查询「水筒」和「ステンレスボトル」在 Amazon 日本站的搜索框联想词。
```

完整结果默认交付 Excel。也可直接运行种子词联想脚本，输出日文原词和联想顺序。

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
