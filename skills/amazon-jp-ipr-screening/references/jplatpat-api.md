# J-PlatPat 接口用法

用 Python 3 运行 [jplatpat.py](../scripts/jplatpat.py)。以下命令从技能目录执行。实际使用商品对应的检索词、准确号码和新证据目录。打开 J-PlatPat 网页前须取得用户的明确要求或同意。

```sh
python3 scripts/jplatpat.py search patent '折り畳み収納箱' --out /tmp/jp-example-patent
python3 scripts/jplatpat.py search design '組立ベッド' --out /tmp/jp-example-design
python3 scripts/jplatpat.py patent-claims '特許4130124' --out /tmp/jp-example-claims
python3 scripts/jplatpat.py design-pdf 1838756 --out /tmp/jp-example-gazette
python3 scripts/jplatpat.py case-status patent '特願2012-184735' --out /tmp/jp-example-patent-status
python3 scripts/jplatpat.py case-status patent '特願2012-184735' --history --out /tmp/jp-example-patent-history
python3 scripts/jplatpat.py case-status design '意匠出願2026-014598' --out /tmp/jp-example-design-status
```

实际交付时，把各次 `--out` 指向报告的 `evidence/` 下不同子目录。先读每次运行保存的 `result.json`，按产品相关性筛选并按日本申请号或登记号去重。详情命令只针对入选候选。特许传 `gazette_keys` 中的准确文献号，优先授权公报。意匠传 `document_id`。报告用案号关联原始 JSON、`claims.txt` 或 `gazette.pdf`，并标明查询方式。

## 查询覆盖

- 简易检索支持日语关键词，默认上限 100 条，可用 `--limit` 调整到 1–500。保留实际请求中的词和筛选条件；空格分隔的词分别交给网页简易检索，不使用未经验证的布尔表达式或分页参数。
- 默认关闭自动缩小范围。遇到 `needs_refinement`，按商品同义词、部件、形状或用途细化，保留原查询的命中数和失败原因。FI、D ターム等脚本未覆盖的专门检索条件，列为待补事项。
- 特许接口的 `server_hits` 可能包含实用新案。只把 `LAW_DIVISION=P` 放入候选；`U` 排除，未知类型单列。命中数、返回条数、在范围内候选数和已审阅条数是不同指标。
- `ok`/`no_result` 表示本次查询或文献读取正常；`complete_within_query` 只表示这一次关键词请求的结果边界，不能证明已经查全。缩小、截断、未分类、验证码、限流和响应异常不能写成零命中。
- 退出码 0 表示 `ok` 或 `no_result`，3 表示部分完成、超限或受阻。先看 `result.json` 的业务结果；HTTP 200 也可能是业务失败。

## 文献与状态

- `patent-claims` 读取指定授权或公开公报的日文权利要求。续页标志存在时保留部分文本。文字版缺失时，列出待补的准确公报号。公开文献与更正后的文本分别核对。
- `design-pdf` 按文献管理信息读取可用的日本国内注册意匠公报 PDF。目前直接下载覆盖已验证的现代 `DG` 格式。其他格式或无缓存的文献列为待补。
- `case-status` 用 `application_number` 查询「経過情報」的基本案件资料，支持形如 `2012-184735` 的日本国内申请号。可从检索或公报结果取得申请号。旧编号、国际意匠等另走查询路径的案件列为待补。脚本核对返回的权利类型和申请号，保存 `case-response.json`。
- `current_legal_status_verified` 为 `true` 时，引用 `stage_label`、适用的详细事件和 `queried_at`，注明这是 J-PlatPat 当次显示。详细事件未映射时保留原代码。意匠可能返回登记资料但没有明确的状态阶段，此时结果为 `partial`，当前状态记“未核实”。候选列表、登记号、预计届满日和公报都不能单独作为当前有效的依据。现任权利人、更正及具体事件仍按实际取得的记录核对。
- 对影响评级的特許用 `--history` 追加查询。`history.registration` 给出公开登记权利人、登记日期、最終納付年分和最大存续期满日；`history.procedure` 统计审查、登记与审判线索，具体事件在 `procedure-response.json`；`history.trials` 提供已返回的审判号、类型、决定日期和处理代码。可分别引用 `registration-response.json`、`procedure-response.json` 和 `trials-response.json`。部分受阻时保留已查资料并记录缺项。公开登记可能滞后，关键权属或效力以所需的原簿等证据核实。[INPIT 讲习会问答](https://www.inpit.go.jp/j-platpat_info/lecture/qa.html)
- `last_paid_year_number` 是缴至第几年度，最大存续期满日单独列示。审判查询为空时，写“本次接口未返回记录”。类型和决定日期用于定位审决公报；遇到订正、无效或更正线索，另核对决定及对应权利要求。`--history` 的结果不包含订正后的全文。[JPO 公报问答](https://www.jpo.go.jp/system/laws/koho/general/koho_faq.html)
- 检索候选、公报和案件结果可带 `case_url`。它按 J-PlatPat 的申请号链接格式生成，直接附在报告中，同时引用实际读取的本地证据。

## 接口与使用边界

这些是 J-PlatPat 网页使用的接口。发生异常时，将具体错误和待补事项写入报告。[J-PlatPat 简易检索](https://www.j-platpat.inpit.go.jp/s0100) · [JPO 特许信息取得 API](https://ip-data.jpo.go.jp/pages/top.html)

| 操作 | 路径 |
| --- | --- |
| 特许候选 | `POST /app/comsrch/wsp0103` |
| 意匠候选 | `POST /app/dtaapi/wsd0203` |
| 匿名请求初始化 | `POST /app/docgw/wsc1101` |
| 特许公报资料与权利要求 | `POST /app/comdocu/wsp1101`、`POST /app/comdocu/wsp1203` |
| 意匠文献管理资料 | `POST /app/dtaapi/wsd0601`，再读取同站公报 PDF |
| 案件基本资料与状态 | `POST /app/docgw/wsh0101` |
| 特許登记、过程和审判资料 | `POST /app/docgw/wsh0105`、`POST /app/docgw/wsh0102`、`POST /app/docgw/wsh0106` |

脚本按网页的匿名初始化流程请求必要的会话数据，不保存会话 Cookie 或初始化响应。逐条执行选品所需的定向查询，不定时采集、不批量下载；遇到验证码或访问限制就停止该请求，不自动重试。参照 [INPIT 使用说明](https://www.inpit.go.jp/j-platpat_info/guide/j-platpat_notice.html)。
