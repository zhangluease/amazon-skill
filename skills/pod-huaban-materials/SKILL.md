---
name: pod-huaban-materials
description: 根据关键词和图片风格，在花瓣网搜索 POD 素材参考，结合找相似与推荐画板扩展，看图初筛、去重并交付带缩略图和来源的候选图集。适用于按需印刷的图案、纹样、插画灵感选择，不把花瓣采集视作已获商用授权。
---

# POD 花瓣素材初筛

将用户的主题和风格变成可比较、可追溯的图片候选。搜索入口为 <https://huaban.com/discovery>；具体取数方式见 [花瓣访问与接口](references/huaban-access.md)。先读取该参考，再选择当前环境确实支持的浏览器或 HTTP 能力。

## 明确筛选目标

必需输入只有关键词、希望的图片风格；缺一项时一次问清。风格可以是自然语言或参考图，例如“猫，复古木刻，黑白单色，适合 T 恤”。若已有上下文则直接使用。

产品类型、配色、横竖比例、是否需要连续纹样、排除元素、候选数量均为可选条件。未提供时先选灵感图，注明尚未按具体印刷区域评估；默认交付 12 张初筛图，数量不足如实说明。

把描述拆成主题、技法、构图、色彩与排除项。先用主题加主要风格搜索；组合过窄时分开查主题词与风格同义词，记录实际查询词。用户明确限定的风格不能因结果少而悄悄放宽。

## 取得候选

1. 优先使用随附 [huaban_browser.cjs](scripts/huaban_browser.cjs) 运行完整 Playwright 无头采集：先进入发现页建立游客会话，再导航到搜索页，监听网页自身的 JSON 与图片响应并缓存预览。本次实测匿名搜索 38 张约 3.3 秒，无需预先登录；相似图加推荐画板约 8 秒取得并缓存 60 个候选。不要以发现页的随机推荐代替关键词搜索。无头访问失败时可用 `--headed`，它仍是批量脚本，不必逐步 Browser Use。
2. 对结果看图，选择少量符合条件的种子。右键“找相似”或详情页“找相似”继续找；详情页“推荐画板”与“所在画板”也可进入。页面不同版本的相似分类名称可能不同，以当前页面为准。
3. 优先从 2–3 张符合风格的种子扩展，比较推荐画板的封面与实际内容，避免只凭画板标题判断。默认最多扩展两层、检查约 60 个去重候选；已有足够多样的好图时提前结束，用户要求更多时再增加。
4. 需要登录的内容才使用用户已提供的站点会话文件 `--state`，或交给可见浏览器由用户完成登录；不因没有 Cookie 就强制登录或注册。本次匿名公开搜索可用，但不能保证所有账号、网络、接口和时间都相同。浏览器脚本也失败时，复用已登录 Browser Use，核对登录提示、验证码或访问错误；不要把 405 简单判断为没登录。
5. 有可用、经验证的 HTTP 会话时，可直接读取 JSON。仅有 Cookie 不能保证成功：可能还有 Bearer、实验头、浏览器校验或设备条件。请求失败时切换已验证浏览器方式，不反复试猜接口或签名。
6. 当前 Browser Use 工具若只允许只读 DOM evaluate，就只读取页面中的图片、链接、文字，不能在该工具 evaluate 中调用 fetch、读取 Cookie、应用内部状态或任意运行时对象。可使用 [extract_cards.js](scripts/extract_cards.js) 的函数表达式提取已呈现的图片卡片。随附独立脚本使用完整 Playwright，可按其正式 API 监听响应；两种执行面不要混淆。

定位约定：点击、填表优先使用 `getByRole` 配合可访问名称、`getByLabel` 等语义定位；根据控件实际语义选择，不套用固定的全局优先级。批量图片读取继续使用网络响应和采集链接等稳定业务属性，保留没有有效 alt 的图片，不将整批提取机械替换为 `getByRole('img')`。语义定位可以直接用于无头脚本，无需额外获取 AX 树快照。详见[定位原则](references/huaban-access.md#定位原则)。

搜索、相似图、推荐画板的来源路径分别保存。API 图片元数据与页面缩略图尺寸分开标记。图片地址的 `auth_key` 和变换后缀原样保留，不能凭 file key 拼接原图，也不能把缩略图放大当作高清素材。

## 看图初筛

必须实际查看图片或联系表，不能用标题、采集量、搜索排名或“相似”标签代替视觉判断。按用户条件解释主题、技法、配色、构图、元素密度的匹配与偏差；相似推荐可能跑题，仍需重新筛选。

结合产品判断可用方向：T 恤关注主体辨识度、留白和底色适配；杯子关注横向展开、把手避让；全幅印花关注元素尺度和重复节奏。未验证平铺接缝时，只能写“适合进一步制作连续纹样”，不能宣称已无缝。实际印刷尺寸和像素已知时才计算像素需求；低清预览不支持印刷质量结论。

按 pin_id、file_id、file key 去重，合并同图的多条来源；必要时再看图合并裁切、换色、加水印等近重复。保留风格多样性，避免整组都是同一图案的小变体。

每张标记：`保留 / 备选 / 排除 / 待看图`，附具体理由。不要编造相似度或客观评分。无法看清或未加载的图标为待核实。

花瓣“版权素材”“商用素材”、会员身份或原站“免费下载”不代表已取得 POD 商品印刷授权。初筛默认标记“灵感参考，授权待核实”，保留作者/原站线索；只有拿到覆盖具体使用方式的授权证据才升级状态。用户要求找可直接商用的素材时，授权可核实性成为筛选条件。不要购买、采集到账号或发布素材来完成普通初筛。

## 交付

默认在当前项目 `reports/pod-materials/<日期>-<主题>/` 保存：

- `gallery.html`：编号图片、风格判断、适用方向、限制、原采集链接，可在浏览器查看。
- `candidates.json`：完整候选与去重后的来源路径，含未入选和待核实项。
- `report.md`：查询词、访问方式、搜索/相似/画板覆盖情况、初筛取舍与缺口。

优先用独立脚本监听浏览器图片响应并读取响应体，保存为本地预览；这样无需再次请求 CDN 或逐张右键。图片 CDN 的独立 HTTP 下载与跨站外链可能失败，取得 `auth_key` 不代表可直接嵌入图集。脚本不可用时才用浏览器已支持的下载功能；当前 Chrome 可按 Shift 右键显示原生菜单，再“图片存储为”。`downloadMedia` 可能已下载文件但仍等待超时，先检查明确的下载结果，不盲目重复。

将浏览器返回或明确保存的本地图片绝对路径填入候选 `local_image`，图集脚本会复制到同目录资源文件夹。没有下载到图时保留链接并说明，不交付空白图集冒充初筛完成。签名图片地址可能过期，需要重访采集刷新。图集默认不展示“排除”项，它们仍保存在完整候选 JSON；`--selected-only` 只展示“保留”。

推荐字段为 `pin_id, pin_url, image_url, file_id, file_key, width, height, dimension_source, title, source_url, board_id, source_paths, decision, visual_notes, product_fit, limitations, rights_status`。尺寸未知写 null。`source_paths` 记录 query、via、parent_pin_id、parent_board_id，保留找图关系。

脚本使用方式：

```sh
node scripts/huaban_browser.cjs --query '狗 动漫' --limit 40 --out /absolute/output/search
python3 scripts/huaban_tools.py normalize /absolute/output/search/response.json --via keyword --query '狗 动漫' --style '动漫' --output candidates.json
python3 scripts/huaban_tools.py api-url search --query '碎花 水彩' --limit 20
python3 scripts/huaban_tools.py api-url similar --pin-id 6498350553
python3 scripts/huaban_tools.py api-url related-boards --pin-id 6498350553 --preview
python3 scripts/huaban_tools.py normalize response.json --via keyword --query '碎花 水彩' --style '水彩小碎花' --output candidates.json
python3 scripts/huaban_tools.py gallery candidates.json --download --output gallery.html
```

以上命令在 skill 目录执行；从其他目录运行时使用脚本绝对路径。独立浏览器脚本需要 Node.js 18+、Chrome 与 Playwright；依赖缺失时在 skill 目录执行 `npm install`，不要修改用户日常 Chrome 配置。它输出 `response.json`、带来源的完整记录、预览缓存、截图与耗时；`--similar <pin_id>`、`--board <board_id>` 可多次提供，用于看图选种子后的扩展。`api-url` 只输出 URL，不发请求。看图后补充判断字段再渲染正式初筛图集。

接口与验证码不可用时交付已有结果和明确缺口。不要用 HTTP 200、登录页、校验脚本或空响应证明成功；不要把“浏览器内成功”写成“Cookie 在独立 HTTP 客户端中已验证成功”。
