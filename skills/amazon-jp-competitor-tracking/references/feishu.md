# 飞书 CLI 工作流

本技能使用官方 `@larksuite/cli`，版本由 `package.json.feishu_cli_version` 固定。优先使用已安装的 `lark-cli`；可通过 `AMAZON_COMPETITOR_FEISHU_CLI` 指向原生可执行文件，也支持本技能用户缓存中的 CLI。CLI 自行管理凭证，不读取浏览器 Cookie 或将令牌放进报告。

## 初始化与授权

首次使用可运行 `npm install -g @larksuite/cli@1.0.100`，或安装到 `~/.amazon-skill/cache/feishu-cli/1.0.100/`。使用官方 CLI 自带的 `skills read lark-shared`、`skills read lark-base` 查看与当前版本匹配的说明。

没有应用配置时，在后台运行 `lark-cli config init --new`，向用户展示原始配置链接，并用 `lark-cli auth qrcode URL --output ./qr.png` 展示 PNG。新增应用涉及创建凭证，需要用户确认后创建；不能自动扩大权限。已有配置不重复创建。

用户身份授权只申请：`offline_access base:app:read base:table:read base:field:read base:record:read base:record:update wiki:wiki:readonly`。使用 `auth login --scope "..." --no-wait --json`，原样展示验证链接与二维码，等待用户确认已完成后，亲自执行 `auth login --device-code CODE`。不要在输出中打印应用密钥或令牌，不复用过期链接。失败按 CLI 的明确错误处理，不切到 bot 身份重试。

Wiki 链接使用已公开的 `GET /open-apis/wiki/v2/spaces/get_node` 解析为实际 Base token，沿用 `wiki:wiki:readonly`；CLI 的新版 `+url-resolve` 可能要求不同的细分权限，不能混淆二者。`feishu_cli.py` 已封装此解析。所有数据操作显式 `--as user`。

## 读取、规划和执行

```sh
python3 "SKILL/scripts/feishu_cli.py" inspect --url "飞书多维表格链接" --out "RUN/feishu"
```

读取 schema 与指定视图范围内的完整记录，检查 `has_more=false`；超过 2000 行时使用返回 offset 继续并核对 rev。输出 `records.json` 和 `plan.json`：`new_rows` 是标题为空的行，`update_rows` 是有标题的行，先处理前者。标题列必须经 schema 确认，不能把投影漏列当作全表新增。

以 record_id 定位行，不按 ASIN 覆盖所有同名记录，不增加记录或字段。一次任务内部重复 ASIN 可共享快照，下一次任务重新采集。每个采集批次不超过原采集器的 200 个 ASIN；使用新报告目录。

完成商品采集与必要的 Amazon 报价补采后，逐条写回并核验：

```sh
python3 "SKILL/scripts/feishu_cli.py" apply-one --url "飞书多维表格链接" \
  --before "RUN/feishu/records.json" --products "RUN/collected/products.json" \
  --record-id "目标记录ID" --out "RUN/writeback"
```

脚本先重读目标行，发现目标字段或 ASIN／标题被其他人改动时停止，不覆盖并发修改。只写 schema 允许的已有字段，保留写前备份和写后核验。CLI 写入成功不证明 record_id 存在，因此必须读回比对。

## 写回口径

- “图片变化”使用七天窗口的 `media_change_observed`：true→`["是"]`，false→`["否"]`，null→保留旧值并记缺项。禁止使用旧 schema 或全周期结果写入。
- 标题、品牌、大小类排名、父体销量、子体阈值、评分、评论数、变体数、当前售价按已验证数据写入；子体阈值保留加号。
- “数量装”只使用标题或指定 ASIN 的变体属性中明确、唯一的件数；混合组件计数不猜测。“是否原装”只反映标题明确声明，无法确认时不改写。
- “库存状态（是/否）断货”：明确缺货→是，明确有货→否，无法配送或来源未知不视为缺货。
- “价格波动（超10%）”比较本次取得价格与原“当前售价”，原价有效且大于零时判断绝对变化比例是否严格大于 10%。原价缺失不写否。
- 历史价格、活动价和活动期间需要各自的明确来源，不用当前价或普通折扣代替。缺失时保留现值并在报告中列出。
- “抓取时间”如果是 `updated_at`，由飞书随实际修改维护，不直接写；ASIN、分组等人工字段保留。

不新增列。测试样本按用户指定范围执行，先一条新记录再一条旧记录；完成采集→写回→读回后交付，不能把本地导出成功描述成飞书写入成功。
