# 卖家精灵日本站关键词挖掘

以日文种子词请求卖家精灵网页的关键词挖掘接口，作为以词找词的独立渠道。每个种子词默认取前三页，每页 50 条，最多 150 条。排序沿用页面的相关度降序，日期条件为最近 30 天；响应中的 `month` 另存为实际数据月份。查询时间与数据月份分别记录。

## 会话准备

已有有效会话时，脚本直接通过 HTTP 查词。首次使用、会话失效或查词遇到 HTTP 401／403 时，脚本自动打开[卖家精灵官网登录页](https://www.sellersprite.com/cn/w/user/login)。请在弹出的浏览器窗口自行完成登录和页面要求的验证。脚本确认当前页能查询后会保存会话、关闭登录窗口并继续取余下的页。

先执行本技能的 `search` 脚本。搜索命令应在用户能看到窗口的桌面进程中运行。出现登录提示后，保持同一进程运行并等待用户完成登录。脚本自动准备 Python Playwright，依次尝试本机 Chrome、Edge 和由 Playwright 管理的 Chromium。缺少配套 Chromium 时按需下载到[公共浏览器缓存目录](client-config.md#写入与检查)。每次执行最多弹窗一次，窗口关闭或等待 5 分钟后结束本渠道，保留已取得的页并继续其他渠道。

会话仅存于 `~/.amazon-skill/cache/keywords/sellersprite-keyword-session.json`，仅当前用户可读写。后续查询复用该会话。登录窗口使用独立浏览器会话。

没有可用桌面时，可选用已授权的日本站关键词挖掘请求另存为 cURL 文本并导入。脚本只读取该请求的 Cookie 和 User-Agent。保存到本机私有临时文件，导入成功后删除临时文件。

```sh
python3 "SKILL/scripts/sellersprite_keyword_miner.py" import-session \
  --curl-file "本机私有请求文件绝对路径"
```

## 浏览器检查

没有弹窗时，先检查 `search` 的终端摘要或结果 JSON。有效会话下会直接查词。遇到浏览器错误时可执行以下命令，实际打开一个测试窗口并自动关闭。加 `--prepare` 可先准备缺失的依赖。Windows 上可将 `python3` 换为 `py -3` 或 `python`。

```sh
python3 "SKILL/scripts/sellersprite_keyword_miner.py" browser-check --prepare
```

成功时输出生效目录、浏览器版本及 `launch_verified`。失败时根据错误码检查桌面、公共配置、Python Playwright、浏览器下载或启动权限。检查在本地完成，无需登录卖家精灵，查询是否授权由原搜索命令验证。

## 查询与结果

```sh
python3 "SKILL/scripts/sellersprite_keyword_miner.py" search \
  --out "RUN" "スピーカー"
```

可一次传多个种子词，每词分别最多三页。需要缩小范围时可用 `--pages 1` 或 `--pages 2`。摘要条数只影响报告展示。

- `sellersprite-keywords.json` 保存来源、查询时间、每个种子词的页码与条数、网页总数、是否还有结果，以及取得的记录和导出表格。Excel 导出失败时，从该 JSON 通过 `export_xlsx.py` 恢复。
- `sellersprite-keywords.xlsx` 保存日文原词、种子词、来源、数据月份、来源页码、月搜索量、购买量和购买率、相关度、PPC 精准竞价及其区间等返回指标。
- 请求间隔 1 秒。登录恢复失败、限流、其他请求错误、分页重复或异常空页会停止本渠道后续请求，保留已取得的有效页面，并继续 Skill 的其他渠道。
- 报告写明「相关度降序前三页，每页 50 条」、实际采集量和网页总数。达到三页且仍有结果时明确注明范围。卖家精灵月搜索量、页面的 ABA 月排名和独立 ABA 日报指标各列保留。缺失值留空，真实零值保留。

主表合并时将取得的候选标为「卖家精灵关键词挖掘」，原词保留并记录证据位置。数据中的品牌、型号和适配关系仍按已有商品证据备注。网页总数代表该次种子词查询条件下的候选数。
