# 飞书 CLI 工作流

本技能使用官方 `@larksuite/cli`，版本由 `package.json.feishu_cli_version` 固定。优先使用已安装的 `lark-cli`；可通过 `AMAZON_COMPETITOR_FEISHU_CLI` 指向原生可执行文件，也支持本技能用户缓存中的 CLI。CLI 自行管理凭证，不读取浏览器 Cookie 或将令牌放进报告。

## 初始化与授权

首次使用可运行 `npm install -g @larksuite/cli@1.0.100`，或安装到 `~/.amazon-skill/cache/feishu-cli/1.0.100/`。使用官方 CLI 自带的 `skills read lark-shared`、`skills read lark-base` 查看与当前版本匹配的说明。

没有应用配置时，在后台运行 `lark-cli config init --new`，向用户展示原始配置链接，并用 `lark-cli auth qrcode URL --output ./qr.png` 展示 PNG。新增应用涉及创建凭证，需要用户确认后创建；不能自动扩大权限。已有配置不重复创建。

用户身份授权只申请：`offline_access base:app:read base:table:read base:field:read base:record:read base:record:update wiki:wiki:readonly`。使用 `auth login --scope "..." --no-wait --json`，原样展示验证链接与二维码，等待用户确认已完成后，亲自执行 `auth login --device-code CODE`。不要在输出中打印应用密钥或令牌，不复用过期链接。失败按 CLI 的明确错误处理，不切到 bot 身份重试。

Wiki 链接使用已公开的 `GET /open-apis/wiki/v2/spaces/get_node` 解析为实际 Base token，沿用 `wiki:wiki:readonly`；CLI 的新版 `+url-resolve` 可能要求不同的细分权限，不能混淆二者。`feishu_cli.py` 已封装此解析。所有数据操作显式 `--as user`。

## 读取、认领和逐条执行

飞书的数据读取、写入、读回核验与结果验收一律使用官方 CLI，不使用 Browser Use 查看表格、截图验收或取得 Cookie。首次授权时给用户 CLI 返回的链接／二维码，由用户自行完成，执行任务等待 CLI 授权结果。

任务分派层必须先规划，再认领。不能让多个执行任务各自读取一遍空标题清单后，绕过队列直接采集。默认由执行器每批调度最多 3 个 ASIN，本批结束后再领取下一批；同机共享 3 个执行名额，多个进程或 Codex 任务合计最多 3 个活跃 ASIN；同机多个 Codex 任务需要使用相同协调目录和同一个 `job_id`。

```sh
# 只规划一次：读取 schema、视图的完整记录，并存入共享队列
python3 "SKILL/scripts/feishu_sync.py" plan --url "飞书多维表格链接" --out "RUN"

# 使用上一步返回的 job_id 执行；同机其他任务也只能领取这个批次
python3 "SKILL/scripts/feishu_sync.py" work --job-id "job_返回的ID" --concurrency 3 --quantity-review-seconds 120

# 查询批次状态和持有者；不传 job-id 可列出活动批次
python3 "SKILL/scripts/feishu_sync.py" status --job-id "job_返回的ID"
python3 "SKILL/scripts/feishu_sync.py" status
```

`--concurrency` 默认为 3，只允许 1–3，可向下调整；共享名额的硬上限始终为 3，其他进程无法叠加突破。执行名额从认领持有到该条处理完释放，进程退出后由内核释放；正在等待名额的任务不先占取 ASIN。卖家精灵采集和登录恢复使用共享锁排队，HTTP 请求仍串行、间隔至少一秒。每个 ASIN 的 CLI 客户端和报告目录独立，事件输出串行化。

只处理某个分组时，`plan --group "Levoit 300-RF-JP/P350-RF"` 按“分组”列精确筛选。`--group-prefix "Levoit 300-RF"` 忽略大小写匹配分组前缀，只有唯一匹配时才生成队列。可同时传入一个或多个 `--asin ASIN`，进一步在该分组内筛选。仅传 `--asin` 时，范围是该视图内这个 ASIN 的所有记录。

`work --max-items 1` 表示本进程至多处理一个 ASIN，失败也计入数量。任务未完成时继续相同 `job_id`，不要重新读取、分派同一批记录。相同表、相同记录集合和日期窗口的活动规划会复用原批次；未指定 as-of 时，同一日本日期内复用原批次固定的结束时刻；不同范围的规划与活动记录重叠时返回 `FEISHU_RECORD_ALREADY_QUEUED` 和原批次 ID，避免无意扩大处理范围。

默认共享状态在 `~/.amazon-skill/state/amazon-jp-competitor-tracking/`，可用 `AMAZON_COMPETITOR_COORDINATION_DIR` 或每条命令的 `--coord-dir` 覆盖。同机执行者必须指向同一目录。不要放入 Git 或共享给其他人；其中有目标记录快照，没有登录凭证。该机制只协调同机进程，不是跨机器的分布式队列。

执行顺序固定如下：

1. SQLite 事务原子认领，绑定 `job_id + phase + ASIN + record_id集合 + owner + generation`。标题为空归新增阶段；全部新增任务结束后才开始旧数据更新。新增表示补齐已有行，不创建行或列。
2. 同时持有一个共享执行名额和该 ASIN 的操作系统文件锁。其他进程不能接管仍存活的持有者；进程退出后内核释放锁，后续执行者增加代次并恢复。每次写回前检查持有者和代次。
3. 通过 CLI 重读已认领记录。ASIN 或原始数据已被其他任务改动则标记 `stale`，在发起采集前停止这条。按 record_id 定位，不按行号或“同名 ASIN 全部更新”。
4. 使用本条独立目录采集一个 ASIN，校验结果的 ASIN、日期窗口、商品数量和缓存校验和。需要主图识别时先完成下方复核。同批次的重复 ASIN 可复用本轮快照，其他批次重新采集。
5. **本 ASIN 采集完成立即写回并读回核验，不等待同批其他 ASIN。** 同 ASIN 的多行逐条写入；其他 ASIN 不等全部采集结束。表级文件锁覆盖重读、并发修改检查、写入、读回全过程。
6. 保存写前数据、拟写字段和写后结果至检查点。服务端已经写入但进程中断时，恢复先核对是否符合原拟写值；一致时确认现状，不重复写入，也不把本轮价格当作新的历史基准。

普通单条失败登记后继续其他条目。`partial` 表示已写回可核实数据但仍有缺项。插件打开登录窗口时，授权提示即时输出到标准错误，采集结果通过标准输出返回。授权等待超时、取消，或账号验证未通过时，保留已取得的有效字段，并将未完成条目标为 `blocked`。处理同一专用会话后运行 `competitor.cjs verify-session --out "NEW_RUN"`，API 检查成功后续跑原 `job_id`。

队列状态为 `pending/running/done/partial/failed/blocked/stale`；`record_verified` 事件逐条报告 ASIN 与 record_id。`resource_busy` 或 `in_progress` 表示仍有工作，不能报告全批完成。`complete_with_gaps` 表示部分条目或字段未完成。已结束的普通失败需要后续新批次重试。

文件锁保护配合本方案的进程。POSIX 上在途飞书 CLI 子进程继承执行名额、ASIN 和表锁，父进程中断后仍保护在途操作。人工改表或旧脚本没有服务端条件更新保证：通过写前检查和读回发现冲突，不能承诺飞书支持事务式“恰好一次”。实际验证平台为 macOS。

只读排查仍可用 `feishu_cli.py inspect --url URL --out NEW_RUN`；低层 `apply-one` 供单条诊断，不用于绕过共享队列分派常规任务。读取超过 2000 行时，CLI 桥接按 offset 继续并核对 rev，完整读取后才规划，不能把投影漏列当作空标题。

## 主图数量复核

Codex 执行 `work` 时保持进程运行，持续读取标准输出。遇到 `package_quantity_review` 事件，打开其中的 `main_image`，根据原图确认商品件数。在事件指定的 `reply_file` 同目录写入临时 JSON 文件，再原子改名为该路径。回应是一个对象，`asin` 和 `main_image` 原样复制自事件。

```json
{"asin":"B0F284QTLM","main_image":"https://m.media-amazon.com/images/I/example.jpg","quantity":1,"evidence":"主图显示一块具有独立外框的滤网"}
```

无法确认时回复 `quantity: null`。等待只占用该 ASIN 的任务名额，其他 ASIN 可继续采集、写回。超时后该条仍写回其余已核验字段。直接从终端运行且没有图像复核时可使用默认值 `--quantity-review-seconds 0`。

## 写回口径

- “图片变化”使用七天窗口的 `media_change_observed`：true→`["是"]`，false→`["否"]`，null→保留旧值并记缺项。禁止使用旧 schema 或全周期结果写入。
- 标题、品牌、大小类排名、父体销量、子体阈值、评分、评论数、变体数、当前售价按已验证数据写入；子体阈值保留加号。
- “数量装”依次使用标题、商品正文规格和该 ASIN 的主图识别结果。件数冲突或无法确认时保留原值。“是否原装”使用标题中的明确声明，无法确认时保留原值。
- “库存状态（是/否）断货”：明确缺货→是，明确有货→否，无法配送或来源未知不视为缺货。
- “当前售价”只使用已核验日本配送的 Amazon 报价。卖家精灵快照价可在本地报告查看。原价有效且大于零时，“价格波动（超10%）”比较新到手的报价与原“当前售价”，绝对变化超过 10% 才填是。缺少任一报价时保留原值。
- 历史价格、活动价和活动期间需要各自的明确来源，不用当前价或普通折扣代替。缺失时保留现值并在报告中列出。
- “抓取时间”如果是 `updated_at`，由飞书随实际修改维护，不直接写；ASIN、分组等人工字段保留。

不新增列。测试样本按用户指定范围执行，先一条新记录再一条旧记录；完成采集→写回→读回后交付，不能把本地导出成功描述成飞书写入成功。
