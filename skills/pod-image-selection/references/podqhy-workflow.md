# PODQHY：选图后的制作与保存

入口为 <https://www.podqhy.com/custom>。先确定产品和素材，再按实际设计面处理。用户要求“自己找图测试”时，主动从站内公开图库选一张合适的装饰图片；不要让用户再选测试素材。保留素材编号、实际原图地址和授权状态。

## 产品选择

选好素材后，先明确本次要制作的 PODQHY 产品，再读取模板和处理产品尺寸。用户已指定的宽高比只用于最终效果图，例如 `4:5`、`16:9`。未指定时，小图以 2000×2000 px 为起点，已达标的视角保留原尺寸。

- 当前任务已经提供产品编号/SKU、商品链接，或此前已明确选定本次使用的产品时，直接沿用，不重复询问。这里指 PODQHY 的目标产品及必要变体，不是 Amazon 竞品 ASIN 或图库素材编号。
- 未明确产品时请用户提供 PODQHY 商品链接或产品编号/SKU。颜色、规格会影响模板时一并询问缺失项。等待产品期间可整理素材来源、下载原图，取得实际模板后再适配和上传设计。
- 只提供产品类别、名称有多个匹配项，或 SKU 与链接指向不同产品时，在站点核对并展示对应候选，请用户选定具体产品后再继续。已提供唯一有效产品时，直接核对进入制作，无需额外确认一次。
- 仅当用户明确授权自行选择产品时，才可以自行挑选并记录选择依据。“自行找图片/素材”的授权不包含自行选择产品。P16900、示例模板 ID 和浏览器碰巧打开的产品都不能自动成为本次目标。

确定后，将用户提供的编号/链接、实际产品名称、详情页 URL 及随后读取的 `pid`、`cid`、`mid` 保存到本任务的 `selected-product.json`，同时记录 `output_aspect_ratio`（例如 `4:5`），有影响模板的变体时一并记录。以页面实际数据建立对应关系，不把用户 SKU 直接当作模板 ID。换产品或变体后重新获取相应模板并重新生成逐面素材，不沿用旧产品尺寸。出图比例只影响末尾交付图片，不改变生产设计面的模板尺寸；用户改比例时可重用已导出的纯效果图重新批处理，无需重新上传设计。

## 登录和产品模板

`podqhy_client.py` 仅发送 HTTP 请求，`pod_artwork.py` 与 `pod_watermark.py` 仅处理本地图片，均不启动或退出浏览器。网页操作复用用户已有浏览器时，任务结束保留设计页并交还控制，不调用浏览器/原有上下文的关闭方法，也不执行退出应用或结束浏览器进程的命令。独立启动的辅助浏览器按 [环境与人工接管](environment.md#独立排障) 正常收尾，不能将退出整个浏览器作为复用标签页任务的固定末尾动作。

1. 复用该站点已登录的可见浏览器。若跳登录页，显示页面让用户完成登录，再继续原流程。不要读取浏览器配置目录、Cookie 数据库或擅自导出认证信息。
2. 打开上述已选产品的详情页，核对产品编号、名称及必要变体后点击“开始定制”。从实际编辑页 DOM 中的内联配置读取 `mid`（模板）、`pid`（产品）、`cid`（颜色），补充到 `selected-product.json`，记录详情页和编辑页 URL。只读取 DOM，不通过页面求值执行应用函数、网络请求或读取认证数据。
3. 使用已观察的接口取得 `flatList`、`viewList`、`color`。脚本自动解开站点的 Base64/URL 编码与 gzip，不需要复制浏览器会话：

```sh
python3 scripts/podqhy_client.py template \
  --model-id 12287 --color-id 32459 --out /absolute/run/template
```

上面的 ID 仅为 **P16900** 验证案例。其他产品必须重新读取，不能替换产品名称却沿用案例模板。

`flatList.data[]` 中 `config.edit` 为真的面都要处理，逐面使用 `flatWidth`、`flatHeight`；`svgUrl` 为裁片遮罩，`viewList` 为真实效果图模板及映射。没有设计面、返回登录 HTML、HTTP 错误或错误状态时停止本次请求，在可见页面检查登录/提示，不要继续猜接口、反复重试或宣称成功。

## 取得素材原图

站内公开图库的图片可以通过实际素材编号请求 `edit/image`。该接口参数由产品模板推导，原图以返回的 `large` 为准，不把缩略图 URL 猜改成原图：

```sh
python3 scripts/podqhy_client.py material \
  --code ID10074496020551716 --template /absolute/run/template/flatList.json \
  --out /absolute/run
```

输入的编号必须来自实际页面。若素材接口需要认证，使用该站点已登录的界面取图；`imageList` 及产品筛选接口可能需要登录，不能把匿名访问失败当作图片不存在。花瓣素材沿用 [选图阶段保存的大图](huaban-workflow.md)，也可使用用户直接提供的本地素材。保留实际来源。

## 水印处理

实际打开选定的大图文件，区分已写在下载像素里的水印、页面叠加的水印和图案本身的文字。文件没有水印时记录检查通过，直接进入尺寸处理。

如果标识确实在下载文件的像素里，在原图尺寸下画完整二值掩码。白色覆盖所有确认的水印，包括浅色斜线、半透明轮廓和被切断的边缘标识。黑色保留其余像素，包括素材本身的文字和装饰。先处理水印，再缩放、裁切或处理透明底。孤立的小块水印可使用 `--method telea`，简单位置也可重复传入 `--rect x0,y0,x1,y1`（右、下边界不包含）。

**满版重复纹样使用 `--method repeat`。** 在原图上找两组重复的同一细节，用像素差测量 `--period dx,dy`。矩形平铺通常分别测量横向和纵向偏移。有一个可靠方向时只传一个。偏移来自相同图案位置，例如两只重复小鹿的同一眼角。脚本核对掩码外清晰纹样是否真的对齐，再用同图内未遮挡的位置补回掩码内像素。仅当剩余水印明确位于纯色背景时，加 `--fill-flat`，让 Telea 补小块平坦区域。

```sh
uv run --with 'pillow>=10,<12' --with 'opencv-python-headless>=4.10,<5' \
  python scripts/pod_watermark.py --source /absolute/run/source.jpg \
  --mask /absolute/run/full-watermark-mask.png --method repeat \
  --period 640,0 --period 0,480 --out /absolute/run/cleaned
```

上面的偏移为示例。脚本在保留尺寸和 alpha 的前提下输出实际掩码、对比图和 `watermark-manifest.json`。若找不到可靠补片，退出码为 `2`，清单标记 `unresolved_regions`，输出带剩余水印的 `source-review.png` 及标出待修位置的 `watermark-unresolved.png`。用这两份文件进入 [AI 局部补修](watermark-ai.md)，按原尺寸检查并补齐浅色线条等漏标。确定性修复已产生错位、接缝或损伤时，改用未处理原图和完整掩码。传入的掩码区域全部处理后输出 `source-clean.png`，状态仍为 `pending_visual_review`。

上传前以 100% 像素查看整张图，逐区检查浅色残影、文字、图案边缘及补片形状。作为无缝纹样使用时，按实测重复单元拼成 2×2，检查横纵接缝。复核通过后才在任务来源记录中写 `watermark_review: passed`，再将实际源文件交给尺寸适配。继续上传的是本轮 `artwork-manifest.json` 指向的文件，已有站内素材按其内容摘要核对。保留原来源与授权状态。去水印检验使用下载文件的像素尺寸，复核后才补足成品像素。

## 图片处理

```sh
uv run --with 'pillow>=10,<12' python scripts/pod_artwork.py \
  --source /absolute/run/cleaned/source-clean.png --template /absolute/run/template/flatList.json \
  --out /absolute/run/prepared --fit cover --background auto
```

上例用于已修复水印的素材，无水印时使用检查通过的原文件。处理规则：

- 共享上传图宽、高均至少 2000 px。任一边不足时按原比例放大到短边 2000 px，记录放大情况。两边达标时保留尺寸。随后按各面实际模板生成裁片参考图。`cover` 居中裁切，`contain` 保留完整图案并留边，两种方式都保留比例。单独交付一张清理好的素材时，可用 `pod_image_size.py --source 原图 --out 新文件`。不足时输出 PNG，达标时按原格式原样复制。
- 缩放后统计透明或近白像素占比，默认超过 8% 判为“非满印候选”；近白容差为每通道距白色不超过 20。白色可能属于图案，像素判定不等于商业意义的满印；连续白底花纹也可能是满幅排版。根据看图结果可用 `--background preserve` 保留背景。
- 已有透明通道则保留。对于不透明、留白明显的白底原图，`auto` 只去除与原图边缘连通的近白区域，保留封闭图形内部的白色，然后按各面缩放裁切，避免先裁切使原本封闭的白色被误去除。`--background remove-white` 可明确选择此分支。复杂摄影/彩色背景不属于该白底算法的可靠范围，需要单独处理并看图复核，不能声称任何背景都已抠净。
- 输出共享上传图 `upload-artwork-<摘要>.png/jpg`、逐面 `flat-<id>.png`、`artwork-manifest.json` 和对比图。上传优先使用 PNG。超过站点 30 MiB 上限时先尝试等尺寸压缩，完全不透明的素材可用等尺寸高质量 JPEG。仍超限时报告文件大小问题，保留当前像素尺寸和透明度。`upload_plan` 记录实际文件、字节数、像素及是否补足到最低标准。逐面 PNG 供核对裁切和放大情况。

PNG 的像素尺寸为设计面需求，不能用随意写入 DPI 元数据冒充清晰度提升。满印/非满印、透明/不透明的每个判断都写入清单，实际处理应以每个设计面为准。

### 像素扩充与超分复核

共享上传图和最终效果图使用同一判断。两边已达标时保留像素。需要放大时，先用 Python 的 Lanczos 等比补足，再按目标输出尺寸查看整图及关键局部。遇到补白的效果图，检查实际产品区域的清晰度。关注主体轮廓、文字的可读性、标识、线条、纹理和颜色过渡。重复图案另外检查一个完整单元及 2×2 拼接的横纵接缝。以预期用途判断清晰度，避免为了滤去正常纹理或达到更大的倍率而额外处理。

如果关键细节仍发虚、粘连或无法辨认，以本轮已清理的原尺寸图尝试 AI 超分。效果图使用实际渲染画布，缺留白时在合成阶段补上。按原比例生成一个能覆盖目标像素的候选。把原图、Python 版本和 AI 候选摆在相同尺寸对照，确认细节确有改善。重点检查主体形状、文字及标识、关键颜色、细线、重复单元和接缝，留意新增笔画、重影、过度锐化或伪纹理。有可见失真时回退到 Python 版本，仍影响用途时在交付中指出具体区域。脚本核验像素和比例，视觉判断以实际图片为准。

采用候选后，使用原始清理图和 `pod_image_size.py` 合成成品。脚本按原图比例将候选归一到原计划尺寸，保留基准图的透明度，并记录处理方法和候选摘要。

```sh
uv run --with 'pillow>=10,<12' python scripts/pod_image_size.py \
  --source /absolute/run/cleaned/source-clean.png \
  --detail-source /absolute/run/ai-candidate.png --out /absolute/run/artwork-checked.png
```

选用共享上传素材时，将复核通过的 `artwork-checked.png` 交给 `pod_artwork.py` 后再上传。对某张最终效果图采用超分时，从原始导出清单重新运行 `pod_effects_layout.py`，为对应视角加 `--detail-source 101=/absolute/run/view-101-ai.png`，多个视角可重复传入。沿用已选出图比例，写入新的成品目录或交付前重建本轮成品。这样会一起更新文件摘要、图集和清单，保留原始捕获及设计核验信息。

## 放入产品并查看效果

`podqhy_client.py` 实现只读模板/素材 API；带登录态的上传、复制和保存默认复用可见页面，不导出认证数据。2026-11-25 已核对编辑页 DOM 及页面引用的 [编辑脚本](https://cdn-cache.8ding.com/js/edit.min.v2.js)：上传控件使用 `POST /edit/image`、文件字段 `imageFile`，保存先序列化各面元素、SVG 和效果图，再提交当前编辑页（脚本 AJAX 的 `url: ''`）。这些是页面实现证据，**不等于独立 API 上传/保存已实测成功**；不能据此猜 POST `/edit/design` 或另造“复制所有”的服务器接口。复制可通过现有页面控件自动完成，无需再次上传。今后若实测接入写入 API，需保留相同的单次上传、逐面核对和保存核验规则。

1. 打开“上传”，**同一图案只上传一次** `artwork-manifest.json.upload_plan.file`。续跑时按带内容摘要的文件名及已记录的站内素材编号核对上传项。上传成功后记录实际编号或页面可辨认的素材条目，后续直接复用。浏览器工具无法选取本地文件时，可用系统文件选择器。
2. 等待“上传成功”，将素材放到当前首面并选择“铺满”（`cover`）或“适应”（`contain`），确定方向及裁切。界面可能额外放大以留出血；P16900 实测 2000 × 2000 的需求对应编辑 SVG 中 2020 × 2020 的图片。不要把这个 1% 固定套到其他模板。
3. 点击“复制至”，默认选择“位置居中”，再点“所有”。菜单对应 `#editFlatCopy1`、`input[name=copyType][value="0"]`、`[data-content-copy] a[data-value="0"]`。复制的是当前面的全部元素；先检查目标面，避免覆盖用户原设计或产生重复图层。已经全部复制时不重复点击。
4. **不同尺寸的面也先复用同一素材**，逐面按模板执行铺满/适应，检查图案方向、裁切和覆盖范围。复制保留纹理大小，位置居中不保证所有尺寸都已铺满；不能复制完成就跳过核对。连续纹样需要纹理大小一致时按实际裁片检查出血范围后调整，不盲目缩放。只有特定面确实需要另一张图或独立构图时才上传额外文件，记录原因。
5. 打开大预览，按下节逐一导出实际效果列表中的所有视角，再进行末尾比例处理。所有面和所有最终图片检查通过后，执行 [命名并保存设计](#命名并保存设计)。
6. 最终回复提供 `final-effects/` 全部独立图片、`effects.html`、实际设计名及保存状态供用户复核。素材对比和操作截图另以记录链接提供。无法一次内嵌全部图片时，提供完整图库及全部文件入口，不能只给几张代表图。保持设计页；不添加购物车、不提交订单。

已有 Playwright Page 或 CUA Tab 时，可调用 `podqhy_design.mjs` 的复制帮助函数，无需重新启动浏览器：

```js
const { copyArtworkToAll } = await import('/absolute/skill/scripts/podqhy_design.mjs');
const copied = await copyArtworkToAll(tab, {
  flatList: JSON.parse(await fs.readFile('/absolute/run/template/flatList.json', 'utf8')),
  sourceSurfaceId: artwork.upload_plan.source_surface_id
});
```

`fs` 为 `node:fs/promises`，`artwork` 为本轮 `artwork-manifest.json`。调用前已在当前源面放好唯一上传图，并确认当前模板与本轮产品一致。函数核对设计面列表、源面非空及目标面为空，点击“所有”后检查各缩略 SVG 是否包含相同素材；返回 `copied_pending_surface_review` 后仍必须完成第 4 步的尺寸和视觉复核。目标已有内容、缩略图结构变化或复制未核验时停止并检查页面，不以重复复制或重复上传作为自动重试。

## 全部效果图导出

效果视角数量来自当前产品的 `viewList.data[]` 和页面 `#editViewList1 li[data-vid]`，与设计面数量不同。逐项核对 ID、数量和顺序，滚动/展开列表取得完整项目；两者不一致时先核对当前产品模板及列表是否加载完整，不能擅自取交集或固定只取前几张。不同视角即使看起来一样也分别交付，不按图片相似度删减。

使用 [podqhy_effects.mjs](../scripts/podqhy_effects.mjs) 导出。模块接收**现有** Playwright Page 或 CUA Tab，不启动或退出浏览器，不调用保存设计/购物车接口：

```js
const { exportEffects } = await import('/absolute/skill/scripts/podqhy_effects.mjs');
const fs = await import('node:fs/promises');
const result = await exportEffects(tab, {
  viewList: JSON.parse(await fs.readFile('/absolute/run/template/viewList.json', 'utf8')),
  out: '/absolute/run/effects'
});
```

`tab` 是当前已登录、已经放好素材的标签页；已有完整 Playwright Page 时可传入 `page`。没有 JS 页面句柄的工具按相同规则逐张导出并保存清单，不能为了运行导出脚本另开一个丢失设计的空白页面。首次使用导出模块，应实际打开导出的图片复核效果。

模块按列表逐一切换，核对当前视角，等待画布渲染后捕获：

- 完整 Playwright 可读取可见 `#editViewBox1 canvas` 的原始 PNG，保存画布原生像素，HTML 缩略图栏和悬浮按钮不会进入图片。
- CUA 只读 DOM 桥不提供 `canvas.toDataURL()`，改用真实 DOM 边界配合完整页面捕获精确截取画布区域；不按固定右边距裁图。画布底部略超出视口时仍须捕获完整范围，并核对图片像素与画布显示范围一致。若控件覆盖画布或捕获范围不完整，报告失败，先通过正常页面操作调整预览再导出，不能交付带控件或截断产品的图片。此方式保存真实截图尺寸及 JPEG 格式，不冒充原生 PNG 或放大补像素。
- 不能把 `viewList.largeImg` 的未定制底图，或右侧约 200 px 的缩略图当成成品大图；也不能用本地示意合成替代站点实际渲染。

在本任务专用 `effects/` 目录输出 `effect-序号-视角ID.png/jpg`、`effects-manifest.json`、`effects.html`。清单逐张记录视角 ID、文件、像素、导出方式和摘要。脚本从各裁片的缩略 SVG 生成 `design_fingerprint`，在导出前后核对。遇到缺图或设计变化时返回 `EFFECT_EXPORT_INCOMPLETE`，保留已导出图片和错误记录，修正后重新导出。正常导出后恢复原选中视角，保留当前编辑页并继续比例处理。

## 末尾统一处理出图比例

全部效果图导出后，将宽、高不足 2000 px 的图片补足再交付。按已给的宽高比处理；未指定比例时，使用方形画布处理未达标视角，已达标视角保留原图。

```sh
uv run --with 'pillow>=10,<12' python scripts/pod_effects_layout.py \
  --manifest /absolute/run/effects/effects-manifest.json \
  --ratio '4:5' --out /absolute/run/final-effects
```

`--ratio` 用于用户已指定比例的任务。小图按比例放大，必要时用白色画布补边保留完整产品。例如不足的方图按 `4:5` 处理后可输出 2000×2500 px。宽、高均已达标且比例合适时，直接复制原始 PNG/JPEG。已达标的图只有在用户指定了不同的宽高比时才补边，图像内容本身保留原像素。图库按实际输出逐张标注尺寸。

对实际放大的视角执行 [像素扩充与超分复核](#像素扩充与超分复核)。只处理清晰度仍需改善的视角，其他视角直接沿用基准流程。

原图保留在 `effects/`，最终图片写入独立的 `final-effects/`，不得覆盖原始导出。生产用的逐面素材 PNG、素材对比和操作截图作为过程文件保留，不混入最终图片集合。最终图库链接全部指向按比例处理后的图片，不能只用 CSS 改显示形状或在原图旁附几张不同尺寸示例。

脚本验证源清单完整、逐张输出并复核数量；最终 `effects-manifest.json` 记录 `stage: final`、`output_aspect_ratio`、实际像素、原文件、内容区域及处理结果。缺少原图、缺视角或处理中有失败时，不能标记最终交付完成。检查所有最终图片的真实像素宽高比与输入一致、视角无缺失、产品与图案正确且没有页面控件，然后继续保存设计，最后交付最终图库/图片供用户复核。

## 命名并保存设计

完成全部效果图与最终比例处理后，默认继续保存设计，不在这一步另加效果确认。用户明确要求“只预览/不保存”时停在预览；保存仍不包含购物车或下单。

1. 点击顶部 `#editSaveDesign1`“保存”，等待 `#editSaveTemplate1` 弹窗。如果本轮弹窗已经打开则直接使用，不重复打开。
2. 根据实际图案填写名称，例如“蓝白水彩格纹-宠物窝”，采用主体/颜色/纹样和必要产品名。用户已指定名称时沿用；不用“New”或空名称，不把无关的编号当作设计主题。名称输入框为 `input[name="name"]`，页面在聚焦时移除 `readonly`，先点击再填写。
3. 核对“是否透明背景”与任务要求一致；无要求时保留原设置，不因为素材带 alpha 就自动勾选。出现“定制区域未铺满，请检查”时，满印任务返回逐面调整，再重新导出受影响的全部效果图、处理比例；非满印留白确属预期时记录已核对的原因再保存，不能盲目忽略警告。
4. 点击弹窗的 `button[data-action="save"]`“保存设计”，等待可见 `#saveSuccessTemplate1` 的“保存成功！”或在“我的设计”核验本轮设计。打开弹窗、填写名称、发送请求、生成本地效果图均不算保存成功。失败或超时先检查页面/“我的设计”，不要自动重复提交造成重复设计。
5. 写入 `design-save.json`，记录实际设计名、产品编辑页、最终效果清单、时间、状态和核验依据；站点实际提供设计 ID/链接时再记录，不猜测。保存状态未核验时保留 `unknown` 或 `failed`，明确交付缺口。

`podqhy_design.mjs` 提供可复用的页面保存函数：

```js
const { saveDesign } = await import('/absolute/skill/scripts/podqhy_design.mjs');
const saved = await saveDesign(tab, {
  name: '蓝白水彩格纹-宠物窝',
  effectsManifest: '/absolute/run/final-effects/effects-manifest.json',
  out: '/absolute/run'
});
```

调用前先做上述视觉与逐面检查。函数验证最终文件摘要、比例、产品、视角与 `design_fingerprint`，复用保存弹窗并核验站点成功提示。换图、调整位置或重新打开编辑页后，若页面校验变化，重新导出再保存。此前缺少设计摘要的旧清单仍可用于比例处理，自动保存前要从当前页重新导出。仅在已核对预期非满印留白时设置 `allowIncompleteCoverage: true`，需要改透明效果背景时才传 `transparentBackground`。超时记录 `unknown`，先在站点查明结果再处理。同一批效果图已核验保存时可复用成功记录。

页面控件已实页核对；帮助函数以本地页面夹具验证，未把夹具测试当作真实账号保存成功。工具无法载入 JS 模块时按同样控件步骤执行，并保留实际核验记录。

## 已验证案例（2026-11-19）

P16900 女士长款睡裤：模板 12287、颜色 32459；`ZuoPian` 45026 与 `YouPian` 45027 均为 2000 × 2000，五个效果视角。站内素材 `ID10074496020551716` 为蓝绿色圆形/点线花纹，原图 5760 × 5760 RGB JPEG。已执行尺寸适配、空白/透明度检测、白底处理、透明 PNG 上传、两面铺满复制，并在站点核验前后成衣效果。此案例用于验证流程，不代表已获用户对最终设计的确认。
