# 花瓣检索与初筛

默认直接使用已选亚马逊主图进行花瓣“以图搜索”。按 `selected-products.json` 中的ASIN匹配 `ranked-products.json` 的 `image_url`，原样使用该URL，不猜测或改写大图后缀。用户直接给参考图时使用本地文件。每张参考图单独检索到一个输出目录，再合并结果，保留来源ASIN与参考图对应关系。

## 采集与扩展

以下命令在skill目录运行，替换图片URL、ASIN和输出路径：

```sh
node scripts/huaban_browser.cjs --image-url 'https://m.media-amazon.com/images/I/example.jpg' --source-asin B000000001 --limit 40 --out /absolute/run/huaban/image-1
python3 scripts/huaban_tools.py normalize /absolute/run/huaban/image-1/response.json --via image_search --output /absolute/run/candidates.json
```

本地参考图使用 `--image /absolute/reference.jpg` 代替 `--image-url`。支持JPG、PNG、GIF、WebP，单张最多12MiB。远程图片下载限定Amazon图片域名，其他来源请提供本地文件。采集器保存 `reference-image.*` 及SHA-256摘要，通过花瓣页面上传控件提交；只有跳转到带有效图片参数的 `/similar` 结果页后才采集。图片检索不采集原发现页推荐卡片；结果保留网站排序，以实际渲染卡片和图片响应记录候选及本地预览。

多张图片依次执行。每张图片结果采集完成后，入口重新随机抽取4000–6000毫秒的冷却时间；下一张图片提交前等待剩余时间。频控状态保存在专用会话目录，跨进程、跨输出目录共享，搜索串行执行，最后一张之后无需额外空等。`timing.json` 的 `image_search_rate_limit` 记录等待时间和本轮冷却时间。关键词检索和画板扩展不受此图片上传频控影响。

花瓣当前的图片上传需要登录。入口自动复用专用的 `huaban-state.json`，也可用 `--state /private/huaban-state.json` 指定用户授权的Playwright storageState文件，与卖家精灵授权独立。缺少或失效时返回 `image_search_login_required`，不自动改用关键词。会话及人工验证处理见 [环境与人工接管](environment.md#花瓣登录会话)。

采集器自动检查环境，默认无头运行。输出 `response.json`、`browser-candidates.json`、`browser-assets/`、参考图和页面截图。多张图归一化时把各 `response.json` 一起传给 `normalize`，按pin_id/file去重并合并参考图来源。

先看候选图，再从2–3张合适的种子扩展：相似页使用 `--similar <pin_id>`，推荐画板使用 `--board <board_id>`，二者可重复提供。画板链接带 `source_file_id` 时同时传 `--source-file-id`，保留原推荐排序。默认最多扩展两层、检查约60个去重候选，有足够多样的素材后停止。

关键词检索保留为用户明确要求时的补充方式：`--query '狗 动漫'`；不能和 `--image`/`--image-url` 混用。仅以文字描述找素材时可沿用关键词方式。读取失败时区分登录要求、访问错误、验证和真实无结果，交付已有结果及缺口。

## 看图与去重

实际查看图片或联系表，判断主体、技法、配色、构图与参考图及用户条件的匹配，不能用标题或相似推荐标签代替看图。商品外形、包装和摄影背景可能主导相似结果，这类结果需标记或排除。按pin_id、file_id、file key去重并合并来源，再检查裁切、换色、水印等近重复。

在 `candidates.json` 为每张填写 `decision`（保留/备选/排除/待看图）、`visual_notes`、`product_fit` 和 `limitations`。看不清的标为待看图。保留参考图摘要、来源ASIN、以图搜索结果页及相似/画板来源关系；关键词补搜时另保留完整搜索词。

优先使用采集器缓存的 `local_image`。图片完整URL及其签名、缩略图后缀原样保留，不拼接猜测原图。API文件尺寸与当前预览尺寸分开记录；缩略图不支持印刷质量结论，未经接缝检查不宣称无缝纹样。

## 交付

用户指定数量时按其要求筛选；未指定时默认12张初筛参考图，数量不足如实说明。候选默认 `rights_status=灵感参考，授权待核实`，只有取得覆盖POD用途的授权证据才更新。

看图并补充字段后生成图集：

```sh
python3 scripts/huaban_tools.py gallery /absolute/run/candidates.json --selected-only --output /absolute/run/gallery.html
```

`gallery.html` 展示保留项及理由；`candidates.json` 保留全部候选、参考图和来源；`report.md` 记录参考图、搜索方式、筛选取舍和缺口。没有可查看的图片时，不宣称初筛完成。
