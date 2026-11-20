# amazon-skill

面向日本亚马逊运营的轻量技能库。

## 公共配置

使用同一易逊服务的技能共享 `~/.amazon-skill/config.json`。`amazon-jp-keywords` 的 ASIN 反查与 `amazon-jp-listing` 的后端校验共同读取 `backend_url/backend_token`，配置一次后在同一系统用户的各 Agent 和各安装目录生效。ABA 使用独立的 `aba_mcp.access_token`，也保存在该文件中。

仓库提供无凭据的 [config.example.json](config.example.json)。初始化公共文件并查看脱敏状态：

```sh
python3 scripts/configure.py init
python3 scripts/configure.py status
```

公共文件结构：

```json
{
  "backend_url": "https://mcp.yixunkuajing.com",
  "backend_token": "",
  "aba_mcp": {
    "url": "https://spapi-mcp.bamboost.info/mcp",
    "access_token": ""
  },
  "browser": {
    "cache_dir": ""
  }
}
```

填写一次相应服务的 token 即可。Agent 接收用户提供的凭据后，通过 `scripts/configure.py configure` 的标准输入合并写入，不回显、不写入命令参数或报告。独立安装某个技能时，可继续用它自己的 `environment.py configure` 或 `listing_environment.py configure`，写入的也是同一个公共文件。更新一个服务不会清空另一个服务的配置；显式写入空字符串可清除该服务的凭据。更换后端地址时同时配置对应 token。

公共文件保存在当前操作系统用户的主目录，macOS/Linux 为 `~/.amazon-skill/config.json`，Windows 为 `%USERPROFILE%\.amazon-skill\config.json`。这里只使用新配置，不迁移或搜索旧技能配置；每个新系统用户需配置一次。J-PlatPat、花瓣的专属访问方式见各自技能说明。

关键词挖掘和 POD 选图从 `browser.cache_dir` 读取共享的 Playwright 浏览器缓存目录。留空时沿用已设置的绝对路径 `PLAYWRIGHT_BROWSERS_PATH`，否则使用当前用户的默认缓存。在 Windows 上通常是 `%LOCALAPPDATA%\ms-playwright`，macOS 是 `~/Library/Caches/ms-playwright`，Linux 是 `$XDG_CACHE_HOME/ms-playwright` 或 `~/.cache/ms-playwright`。改目录时向 `configure` 的标准输入传入 `{"browser":{"cache_dir":"本机绝对路径"}}`。同一系统用户下的各项目共用该目录，相同的浏览器版本会复用。站点登录会话各自保存。

`python3 scripts/configure.py browser-status` 查看生效路径。验证浏览器能实际启动，使用[关键词浏览器检查](skills/amazon-jp-keywords/references/sellersprite-keyword-miner.md#浏览器检查)或 [POD 环境检查](skills/pod-image-selection/references/environment.md#独立排障)。

公共读取逻辑维护在 `shared/amazon_config.py`，三个相关技能随包包含同版本副本，因此单独安装一个技能也能使用公共文件。修改公共逻辑后运行 `python3 scripts/sync_shared_config.py`，用 `--check` 检查发布副本是否同步。

## POD 选图

[pod-image-selection](skills/pod-image-selection/SKILL.md) 输入关键词，匿名获取Amazon美国站搜索结果ASIN，用卖家精灵查询父体销量和主图，按销量降序展示全部有效候选；用户多选后直接以主图到花瓣以图搜图，再看图初筛参考素材。花瓣图片上传需要登录，专用会话可复用。默认搜索第一页，配送目的地（Deliver to）按国家选择美国，不指定邮编；可通过 `--amazon-country JP` 改为配送到日本，搜索站点仍为Amazon.com。需要美国具体地区时可选用 `--amazon-zip`；Amazon和销量取数使用HTTP。

```text
请使用 $pod-image-selection，关键词：pet house。
```

将完整文件夹交给Agent安装即可。入口自动处理依赖和会话；需要真人登录时按提示操作。详见[竞品查询](skills/pod-image-selection/references/sellersprite-access.md)、[花瓣初筛](skills/pod-image-selection/references/huaban-workflow.md)和[环境与人工接管](skills/pod-image-selection/references/environment.md)。完整取数与自动授权的真实账号流程仍在试用验证中。

选图后可继续制作 [PODQHY 成衣预览](skills/pod-image-selection/references/podqhy-workflow.md)：收集产品编号/SKU 或商品链接时一并记录最终出图宽高比，已有信息直接沿用；未指定产品时主动询问并等待选择，再通过接口读取该产品全部设计面尺寸，用 Pillow 适配图片、检测空白与透明度、处理透明底，再放入产品检查前后效果。P16900 仅为测试案例，不作为默认产品。当前模板和原图读取使用 HTTP，带登录态的上传和效果预览复用可见页面；需要登录时交给用户完成。先导出实际列表中全部视角的纯效果图，排除缩略图栏、放大按钮等控件；最后增加统一比例处理环节，将整套图片按用户比例生成到 `final-effects/`，附完整图库 `effects.html` 和数量清单，再等待用户确认效果。

## 日本站关键词查找

[amazon-jp-keywords](skills/amazon-jp-keywords/SKILL.md)（原 `amazon-jp-listing-asin`，中文名「日本亚马逊关键词调研」）支持两种找词方式，负责关键词与广告候选词采集。

- **根据 ASIN 找词**。用竞品 ASIN 或 `amazon.co.jp` 商品链接，通过老陈的易逊后端反查，并用 ABA 全站搜索词中命中竞品点击 ASIN 的记录补充候选。
- **以词找词**。用日文种子词或配件型号查 Amazon 日本站搜索框联想、卖家精灵关键词挖掘、厂家官网和 ABA 全站搜索词，四个渠道都尝试，再合并去重。卖家精灵使用本机登录会话，以独立 HTTP 采集每词前三页（每页 50 条），输出月搜索量和竞价明细。查配件时核对在售、停售和停产机型。

官网原词和官网组合词在采集后按目标商品及购买意图洗词：泛功能描述、与整机目标不符的零部件词及缺少型号依据的词不直接进入主表，完整候选、结论和原因保存在官网明细。Amazon 搜索框联想（关联词）不洗词，易逊与 ABA 候选也按原有规则保留。多来源同词不会因官网筛选被误删，详见[官网词相关性筛选](skills/amazon-jp-keywords/references/keyword-cleaning.md)。

将整个 `skills/amazon-jp-keywords/` 文件夹放入所用 Agent 的技能目录。脚本在首次使用时按需准备依赖，易逊与 ABA 凭据统一读取下述公共配置。用 `environment.py configure` 从标准输入合并更新，用 `environment.py config-status` 查看脱敏状态。

ABA 默认通过随附 `aba_client.py` CLI 直连服务，无需 Agent 注册 MCP，也无需安装 MCP SDK；只需 Python 3.9+。运行 `python3 skills/amazon-jp-keywords/scripts/aba_client.py status` 实测连接，`call` 执行工具，`collect-query` 完整采集并支持分页中断续取。选择原生 MCP 时也可通过 `aba_mcp_headers.py` 读取同一配置，详见[客户端配置](skills/amazon-jp-keywords/references/client-config.md)。

ABA 使用已连接的 `https://spapi-mcp.bamboost.info/mcp`，无需自有 ASIN；连接不可用或报表未完成时说明状态，继续交付已有候选。ABA 排名和份额分别标注，不当作月搜索量或易逊流量占比。报表与筛选任务保存 ID，可续查并复用压缩源缓存。

ABA 默认目标日期为查询日前 7 天，不含查询当天。例如 11 月 11 日查询对应 11 月 4–10 日，拆成 7 份 `DAY` 报表。`aba_client.py plan-window` 可生成逐日报表参数；默认查询日按北京时间，也可用 `--as-of YYYY-MM-DD` 指定。关键词合并去重，指标保留逐日明细。未取得的日期明确标注缺口，不用零补齐，不把每日指标相加或平均成 7 日指标。用户指定完整周、月或季度时仍支持相应单周期。

```text
请使用 $amazon-jp-keywords 反查我提供的日本亚马逊竞品 ASIN，整理前 20 个日文流量词和月搜索量。
请使用 $amazon-jp-keywords 以「水筒」和「ステンレスボトル」找词，合并 Amazon 日本站搜索框联想和厂家官网结果，官网词洗词、Amazon 联想词全部保留，交付主表和完整来源明细。
请使用 $amazon-jp-keywords 以 H060518 加湿器滤芯找词。查询 Amazon 日本站联想和大日官网，核对在售、停售及停产机型，用型号和官方部件名扩词，去重后附官方证据。
```

两种方式都交付 Excel。ASIN 反查交付 `keywords.xlsx`，以词找词交付 `seed-keywords.xlsx`；官网完整候选及洗词结论另存 `official-keywords.xlsx`，已取得的 ABA 商品与周期明细另存 `aba-keywords.xlsx`。报告说明官网洗前、保留、剔除及待核实数量。同一任务执行两种方式时，两份主表都交付。单独运行以下联想脚本可查看日文原词和联想顺序。

```sh
python3 skills/amazon-jp-keywords/scripts/amazon_jp_autocomplete.py \
  --output reports/jp-keywords/example/autocomplete.xlsx "水筒"
```

## 日本站 Listing 生成

[amazon-jp-listing](skills/amazon-jp-listing/SKILL.md) 接收关键词 XLSX/CSV/TSV、真实本品资料与图片，完整导入后按含义分组与安排字段，生成日文标题、Highlight、五点、长描、后台搜索词、属性建议，以及主附图和 A+ 制作策划。默认信任用户给定关键词、不做二次核验；文件名任意，按内容识别；编号按语义区分本品标识、适配对象和规格数值，不按固定前缀判断。耗材按型号适配、规格适配或通用用途组织五点，明确适配名单完整保留。每次处理一个商品，交付 `listing.md`、`listing.json` 和中文 HTML 验收报告 `report.html`，不加步骤编号前缀。

仅面向新版：标题最多 75 字符，尽量在前 35–40 字符交代身份、核心品名和关键编号；标题下独立小标题最多 125 字符，补充适用对象、代表型号及真实包装数量／组成；材质尺寸在五点正文，五点以相关词和短主题开头。默认主题为型号与套装、性能价值、适配、更换条件、品质与购买事项，可按资料和长名单调整。字段布局须对照原词指标与本品事实复审，报告将标题和灰字小标题相邻展示。 按对象型号选购的耗材通常用2–3条五点按系列完整列出已确认机型，校验缺项、后缀和来源引用；每条五点按全文计最多 250 字符，包含型号及分隔符。位置不足时优先合并或省略独立性能点，五条仍容纳不下时保留完整草稿并标出超限。

流程：产品画像 → 完整导入词表 → 可用／暂缓／剔除三类词池 → 卖点与关键词布局 → 日文文案及图片策划 → 覆盖与词位检查 → 本地／后端校验和内容复核。给定词按默认可信原则使用，检查本品范围及具体输入冲突。

基于[老陈用户表格版](https://github.com/chenqinglin2019-tech/Laochen-Amazon-project/tree/87dead1472eb4ddfd28f99fd47a9593b5c3f7771/skills/lc_amazon_listing)的固定版本实现，只面向日本站。本地导入、筛词和渲染只需 Python 3.9+ 标准库；后端校验自动准备并核对 CLI，不需要注册 MCP，读取公共 `~/.amazon-skill/config.json` 的 `backend_token`。后端入口仅提供日本站 validate；买家问题根据本品资料整理，不另采集 QA。缺少实际后端或内容复核记录时，报告保留未完成状态。

```text
请使用 $amazon-jp-listing，根据我提供的关键词文件（名称不限）和耗材图片、规格及包装清单，为日本站生成完整日文 Listing，完成意图筛词、主附图与 A+ 策划，并交付文案和验收报告。
```

关键词与 Listing 技能共享同一个公共配置文件；分别安装到 Trae、Codex 或不同项目目录时，同一系统用户配置一次即可使用。两个技能的职责、来源与执行命令分别见各自 `SKILL.md`。

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
