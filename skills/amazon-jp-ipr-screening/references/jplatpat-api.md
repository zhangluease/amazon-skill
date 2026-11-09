# J-PlatPat 接口用法

用 Python 3 运行 [jplatpat.py](../scripts/jplatpat.py)。以下命令从技能目录执行，示例只说明输入格式；实际使用商品对应的检索词、准确号码和新证据目录。

```sh
python3 scripts/jplatpat.py search patent '折り畳み収納箱' --out /tmp/jp-example-patent
python3 scripts/jplatpat.py search design '組立ベッド' --out /tmp/jp-example-design
python3 scripts/jplatpat.py patent-claims '特許4130124' --out /tmp/jp-example-claims
python3 scripts/jplatpat.py design-pdf 1838756 --out /tmp/jp-example-gazette
```

实际交付时，把各次 `--out` 指向报告的 `evidence/` 下不同子目录。先读每次运行保存的 `result.json`，按产品相关性筛选并按日本申请号或登记号去重。详情命令只针对入选候选：特许传 `gazette_keys` 中的准确文献号，优先授权公报；意匠传 `document_id`。留存原始 JSON；权利要求读 `claims.txt`，意匠附图读 `gazette.pdf`。

## 查询覆盖

- 简易检索支持日语关键词，默认上限 100 条，可用 `--limit` 调整到 1–500。保留实际请求中的词和筛选条件；空格分隔的词分别交给网页简易检索，不使用未经验证的布尔表达式或分页参数。
- 默认关闭自动缩小范围。遇到 `needs_refinement`，按商品同义词、部件、形状或用途细化，保留原查询的命中数和失败原因。需要 FI、D ターム等专门检索条件时，转对应的官方检索页补查。
- 特许接口的 `server_hits` 可能包含实用新案。只把 `LAW_DIVISION=P` 放入候选；`U` 排除，未知类型单列。命中数、返回条数、在范围内候选数和已审阅条数是不同指标。
- `ok`/`no_result` 表示本次查询或文献读取正常；`complete_within_query` 只表示这一次关键词请求的结果边界，不能证明已经查全。缩小、截断、未分类、验证码、限流和响应异常不能写成零命中。
- 退出码 0 表示 `ok` 或 `no_result`，3 表示部分完成、超限或受阻。先看 `result.json` 的业务结果；HTTP 200 也可能是业务失败。

## 文献与状态

- `patent-claims` 读取指定授权或公开公报的日文权利要求。续页标志存在时保留部分文本并补查；公报没返回文字版时按准确号码读官方 PDF。公开文献与更正后的文本分别核对。
- `design-pdf` 按文献管理信息读取可用的日本国内注册意匠公报 PDF。目前直接下载覆盖已验证的现代 `DG` 格式；历史、秘密、其他类型或无缓存的文献按需用网页补查。
- 候选列表、公报或其中的状态码不能证明今日仍有效。只对影响评级的入选候选查看 J-PlatPat「経過情報」，核对当前状态、权利人和更正等事项。核对不到就把缺项标为待定。

## 接口与使用边界

这些是 [J-PlatPat 简易检索](https://www.j-platpat.inpit.go.jp/s0100) 网页使用的接口，与 [JPO 另行提供的特许信息取得 API](https://ip-data.jpo.go.jp/pages/top.html) 是两套服务。网页接口可能变化，异常时用正常网页流程或披露缺口。

| 操作 | 路径 |
| --- | --- |
| 特许候选 | `POST /app/comsrch/wsp0103` |
| 意匠候选 | `POST /app/dtaapi/wsd0203` |
| 匿名请求初始化 | `POST /app/docgw/wsc1101` |
| 特许公报资料与权利要求 | `POST /app/comdocu/wsp1101`、`POST /app/comdocu/wsp1203` |
| 意匠文献管理资料 | `POST /app/dtaapi/wsd0601`，再读取同站公报 PDF |

脚本按网页的匿名初始化流程请求必要的会话数据，不保存会话 Cookie 或初始化响应。逐条执行选品所需的定向查询，不定时采集、不批量下载；遇到验证码或访问限制就停止该请求，不自动重试。参照 [INPIT 使用说明](https://www.inpit.go.jp/j-platpat_info/guide/j-platpat_notice.html)。
