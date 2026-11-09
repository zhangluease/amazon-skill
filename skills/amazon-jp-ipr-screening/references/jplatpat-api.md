# J-PlatPat 接口用法

用 Python 3 运行 [jplatpat.py](../scripts/jplatpat.py)。以下命令从技能目录执行。实际使用商品对应的检索词、准确号码和新证据目录。打开 J-PlatPat 网页前须取得用户的明确要求或同意。

```sh
python3 scripts/jplatpat.py search patent '折り畳み収納箱' --out /tmp/jp-example-patent
python3 scripts/jplatpat.py search design '組立ベッド' --out /tmp/jp-example-design
python3 scripts/jplatpat.py patent-claims '特許4130124' --out /tmp/jp-example-claims
python3 scripts/jplatpat.py patent-claims '実登3055073' --out /tmp/jp-example-utility-claims
python3 scripts/jplatpat.py design-pdf 1838756 --out /tmp/jp-example-gazette
python3 scripts/jplatpat.py case-status patent '特願2012-184735' --out /tmp/jp-example-patent-status
python3 scripts/jplatpat.py case-status patent '特願2012-184735' --history --out /tmp/jp-example-patent-history
python3 scripts/jplatpat.py case-status utility-model '実願平10-004399' --history --out /tmp/jp-example-utility-history
python3 scripts/jplatpat.py case-status design '意匠出願2026-014598' --out /tmp/jp-example-design-status
```

实际交付时，把各次 `--out` 指向报告的 `evidence/` 下不同子目录。逐条读 `result.json` 中保留的候选书目，按权利类型及日本申请号或登记号去重。详情命令用于相关候选。特許和実用新案传 `gazette_keys` 中的准确文献号，优先授权或登记公报。意匠传 `document_id`。报告引用对应的原始 JSON、`claims.txt` 或 `gazette.pdf`。

## 查询覆盖

- `--limit` 控制本地输出，默认 500 条，可指定 1–500。请求 J-PlatPat 简易检索时使用 3,000 条上限，再按响应顺序截取 `result.json` 中的候选，保留完整原始响应。空格分隔的词分别交给简易检索。
- 原查询超过 3,000 条且接口拒绝返回列表时，脚本按站点的日期自动缩小功能补发一次，并保存 `search-narrowed-response.json`。报告同时记录原命中数、缩小后范围和本地截断。仍超限或失败时，按商品同义词、部件或用途补查并保留原证据。[INPIT 操作手册](https://www.inpit.go.jp/content/100884844.pdf)
- `server_hits` 是原查询命中数，`rows_returned` 是接口交付的列表条数，`rows_selected` 是本地保留条数，`locally_truncated_rows` 是本地截去的条数。`search patent` 同时收录 `LAW_DIVISION=P` 的特許和 `U` 的実用新案，以 `right_type` 标记并分别计数。未知类型在原始响应中保留并单列。实际审阅数按本轮操作记录。
- `ok`/`no_result` 表示本次查询或文献读取正常；`complete_within_query` 只表示这一次关键词请求的结果边界，不能证明已经查全。缩小、截断、未分类、验证码、限流和响应异常不能写成零命中。
- 退出码 0 表示 `ok` 或 `no_result`，3 表示部分完成、超限或受阻。两种退出码都读取 `result.json`。遇到 `partial` 时，继续审阅已保留的候选或记录，并在报告中注明缺口。

## 文献与状态

- `patent-claims` 读取特許或実用新案指定授权、登记或公开公报的日文权利要求。续页标志存在时保留部分文本。文字版缺失时，列出待补的准确公报号。公开文献与更正后的文本分别核对。
- `design-pdf` 按文献管理信息读取可用的日本国内注册意匠公报 PDF。目前直接下载覆盖已验证的现代 `DG` 格式。其他格式或无缓存的文献列为待补。
- `case-status` 用 `application_number` 查询「経過情報」的基本案件资料。接受形如 `2012-184735` 的日本国内申请号，実用新案也接受形如 `実願平10-004399` 的和历申请号。可从检索或公报结果取得申请号。脚本核对返回的权利类型和申请号，保存 `case-response.json`。另走查询路径的案件列为待补。
- `current_legal_status_verified` 为 `true` 时，引用 J-PlatPat 返回的 `stage_label`、适用事件和 `queried_at`。実用新案和意匠可能缺少明确状态阶段，结果为 `partial`。実用新案可另引用接口返回的明确消灭日 `extinction_date`。仅有登记号、预计届满日、公报或列表状态码时，当前状态记“未核实”。
- 对影响评级的特許和実用新案用 `--history` 追加查询。`history.registration` 给出公开登记权利人、登记日期、最終納付年分和最大存续期满日。`history.procedure` 统计审查、登记与审判线索，`history.trials` 提供已返回的审判号、类型、决定日期和处理代码。引用对应的 `registration-response.json`、`procedure-response.json` 和 `trials-response.json`。部分受阻时保留已查资料并记录缺项。公开登记可能滞后，关键权属或效力以所需的原簿等证据核实。[INPIT 讲习会问答](https://www.inpit.go.jp/j-platpat_info/lecture/qa.html)
- `last_paid_year_number` 是缴至第几年度，最大存续期满日单独列示。审判查询为空时，写“本次接口未返回记录”。类型和决定日期用于定位审决公报；遇到订正、无效或更正线索，另核对决定及对应权利要求。`--history` 的结果不包含订正后的全文。[JPO 公报问答](https://www.jpo.go.jp/system/laws/koho/general/koho_faq.html)
- 检索候选、公报和案件结果可带 `case_url`。它按 J-PlatPat 的申请号链接格式生成，直接附在报告中，同时引用实际读取的本地证据。

## 接口与使用边界

这些是 J-PlatPat 网页使用的接口。发生异常时，将具体错误和待补事项写入报告。[J-PlatPat 简易检索](https://www.j-platpat.inpit.go.jp/s0100) · [JPO 特许信息取得 API](https://ip-data.jpo.go.jp/pages/top.html)

| 操作 | 路径 |
| --- | --- |
| 特許和実用新案候选 | `POST /app/comsrch/wsp0103` |
| 意匠候选 | `POST /app/dtaapi/wsd0203` |
| 匿名请求初始化 | `POST /app/docgw/wsc1101` |
| 特許和実用新案公报资料与权利要求 | `POST /app/comdocu/wsp1101`、`POST /app/comdocu/wsp1203` |
| 意匠文献管理资料 | `POST /app/dtaapi/wsd0601`，再读取同站公报 PDF |
| 案件基本资料与状态 | `POST /app/docgw/wsh0101` |
| 特許和実用新案登记、过程和审判资料 | `POST /app/docgw/wsh0105`、`POST /app/docgw/wsh0102`、`POST /app/docgw/wsh0106` |

脚本按网页的匿名初始化流程请求必要的会话数据，不保存会话 Cookie 或初始化响应。逐条执行选品所需的定向查询，不定时采集、不批量下载；遇到验证码或访问限制就停止该请求，不自动重试。参照 [INPIT 使用说明](https://www.inpit.go.jp/j-platpat_info/guide/j-platpat_notice.html)。
