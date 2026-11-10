---
name: amazon-jp-listing-asin
description: 查找日本亚马逊日文关键词。按竞品 ASIN 或 amazon.co.jp 商品链接通过老陈易逊后端反查流量词，或按日文种子词请求 Amazon 搜索框联想。支持按本品相关性筛词。
---

# 日本亚马逊关键词查找

任务在交付关键词表后结束。标题、五点、后台搜索词、图片和 A+ 属于后续 Listing 工作。

## 输入

- 用户给竞品 ASIN 或 `amazon.co.jp` 商品链接时走 ASIN 反查。用户给种子词或要求采集搜索框联想时走种子词联想。两种输入都给了就分别查询，按来源交付。
- 本品名称、用途、规格、必保词和禁用词可用于筛词。已有有效 ASIN 或种子词即可先查。
- 在 `reports/jp-keywords/` 下创建独立任务目录 `RUN`。下文命令中的 `SKILL` 指当前技能根目录，将路径替换为实际绝对路径。
- 使用当前 Agent 可调用的 Python 3.9+ 运行随附脚本。脚本按需准备运行环境，安装和复用成功时直接继续查询或导出。只在缺少 Python、依赖准备失败或需要用户补充凭据时说明具体问题。
- 完整结果默认交付 Excel（`.xlsx`）。

## ASIN 反查

从 `/dp/<ASIN>` 或 `/gp/product/<ASIN>` 商品链接提取 ASIN。去除空白、转为大写，校验为 10 位字母或数字，按输入顺序去重。列出被跳过的无效输入和非日本站链接。单次查询最多 20 个去重后的 ASIN，超出时请用户选定本轮 ASIN。

1. 用随附的 [ASIN 反查脚本](scripts/environment.py) 向老陈的易逊后端整组反查。脚本按平台自动准备并校验固定版本的 CLI 和调用脚本，缓存到本机用户目录。ASIN 用英文逗号连接。

   ```sh
   python3 "SKILL/scripts/environment.py" expand \
     --asins "ASIN1,ASIN2" --output "RUN/02_kw_raw.json"
   ```

2. 优先复用本技能或本机已配置的易逊凭据。命令提示缺少凭据时，只请用户配置易逊 token。通过 `python3 "SKILL/scripts/environment.py" configure` 的标准输入传递含 `backend_token` 的 JSON，保存到用户目录 `~/.amazon-jp-listing-asin/config.json`。凭据只通过安全的标准输入或本机配置传递，避免出现在命令参数、日志和报告中。
3. 默认复用同一输出父目录内、相同 ASIN 集合且同为日本站的 7 天内结果。用户要求刷新时用 `--reuse-days 0`。记录站点、请求 ASIN、实际查询或缓存来源，以及响应明确提供的数据日期。查询日期与数据日期分开记录。
4. 同时检查命令摘要和 `02_kw_raw.json`。缓存命中时仍检查业务状态和错误字段。区分环境准备、鉴权、额度、网络、部分结果和后端明确返回的无数据。响应缺失、结构异常或失败时，按实际状态交付已有有效词，避免把失败当作零匹配。超时或提交状态未知时，先核对请求状态。

## 种子词联想

使用随附的 [Python 脚本](scripts/amazon_jp_autocomplete.py) 直连 `completion.amazon.co.jp`。查询无需浏览器、Amazon 登录或易逊 token。每个种子词作为一次搜索前缀请求，保留接口返回顺序。

```sh
python3 "SKILL/scripts/amazon_jp_autocomplete.py" \
  --output "RUN/autocomplete.xlsx" "水筒" "ステンレスボトル"
```

查看终端 JSON 摘要。查询成功但无候选会标为 `no_matches`，部分失败时仍保存已成功取得的候选。遇到 HTTP 403 或 429 时，脚本停止剩余请求并标为 `skipped`。Excel 每行记录来源、种子词、联想位置和日文原词。同一候选来自多个种子词时，保留每个种子词各自的位置。联想来源的月搜索量和流量占比留空。

## 整理与交付

- ASIN 反查的关键词取自响应的 `keywords` 数组（字符串或含 `keyword` 的对象），缺少该数组时读取 `raw.keyword_data`。按原词关联 `raw.keyword_data` 里的指标。月搜索量使用 `searches` 或 `monthly_searches`，流量占比使用 `trafficPercentage` 或 `traffic_percentage`。指标缺失标记「未返回」，保留真实的零。逐词命中 ASIN 和排名仅在响应有明确对应关系时列出，整组查询的输入 ASIN 另列。
- 保留服务端返回的日文原词。仅合并去除首尾空白后完全相同的词，保留原词写法。平假名、片假名、汉字、空格差异和长尾变体可归到同一意图组，仍逐词保留指标。重复记录的流量占比不相加。指标冲突时标注各自来源或口径。
- 用户提供本品资料时，可按品名、属性、用途、人群或场景做意图分组。将有本品事实支持的词标记为「相关」，事实不足或词义有歧义的标记为「待确认」，明确不符或被用户禁用的标记为「排除」，并简述依据。竞品品牌词单独标记。词短、搜索量低和日语写法差异均可保留。有必保词未命中时，单独标记为用户提供、查询未命中。
- 默认用中文说明，每个来源展示最多 20 个日文关键词或用户指定的数量。ASIN 反查按已知月搜索量降序，完整结果交付 `keywords.xlsx`。种子词联想按种子词和返回位置展示，完整结果交付 `autocomplete.xlsx`。摘要中的完整结果链接指向对应工作簿。说明各来源的条数和查询状态。已做相关性判断时优先展示相关词，其他词保留分类。

### 导出 ASIN 反查结果

将整理后的完整表写成临时 JSON，包含 `headers`（表头字符串数组）、`rows`（按表头顺序排列的二维数组），以及可选的 `percentage_columns`（需要显示为百分比的表头名称数组）。月搜索量用数字，缺失值可留空。已确认按比例返回的流量占比用数值和 Excel 百分比格式，例如 `0.0155` 显示为 `1.55%`。口径未确认时保留原值，商品型号和 ASIN 保留为文本。

```sh
python3 "SKILL/scripts/export_xlsx.py" --input "关键词表临时JSON绝对路径" \
  --output "RUN/keywords.xlsx"
```

工作流参考 [lc-amazon-listing-asin 的扩词和筛词步骤](https://github.com/chenqinglin2019-tech/Laochen-Amazon-project/blob/main/skills/lc-amazon-listing-asin/INSTRUCTIONS.md#2-整组扩词1-次请求)。
