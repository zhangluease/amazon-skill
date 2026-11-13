# 花瓣检索与初筛

使用已选竞品图片提取的主题、风格、配色和构图搜索。直接找素材时沿用用户提供的条件。不同视觉方向分组检索，每组记录来源ASIN；组合过窄时拆词或换同义词，保留用户明确限定的风格。

## 采集与扩展

以下命令在skill目录运行，替换查询词和输出路径：

```sh
node scripts/huaban_browser.cjs --query '狗 动漫' --limit 40 --out /absolute/run/huaban/search
python3 scripts/huaban_tools.py normalize /absolute/run/huaban/search/response.json --via keyword --query '狗 动漫' --style '动漫' --output /absolute/run/candidates.json
```

采集器自动检查环境，默认匿名无头运行，监听页面JSON和图片响应，缓存本地预览。输出 `response.json`、`browser-candidates.json`、`browser-assets/` 和页面截图。人工验证处理见 [环境与人工接管](environment.md#花瓣人机验证接管)。

先看候选图，再从2–3张合适的种子扩展：相似页使用 `--similar <pin_id>`，推荐画板使用 `--board <board_id>`，二者可重复提供。画板链接带 `source_file_id` 时同时传 `--source-file-id`，保留原推荐排序。默认最多扩展两层、检查约60个去重候选，有足够多样的素材后停止。

需要登录内容时，可使用用户提供的花瓣会话文件 `--state`；它与卖家精灵授权独立。读取失败时区分访问错误、验证和真实无结果，交付已有结果及缺口。

## 看图与去重

实际查看图片或联系表，判断主体、技法、配色、构图与用户条件的匹配，不能用标题或相似推荐标签代替看图。按pin_id、file_id、file key去重并合并来源，再检查裁切、换色、水印等近重复。

在 `candidates.json` 为每张填写 `decision`（保留/备选/排除/待看图）、`visual_notes`、`product_fit` 和 `limitations`。看不清的标为待看图。保留完整搜索词及搜索/相似/画板来源关系。

优先使用采集器缓存的 `local_image`。图片完整URL及其签名、缩略图后缀原样保留，不拼接猜测原图。API文件尺寸与当前预览尺寸分开记录；缩略图不支持印刷质量结论，未经接缝检查不宣称无缝纹样。

## 交付

用户指定数量时按其要求筛选；未指定时默认12张初筛参考图，数量不足如实说明。候选默认 `rights_status=灵感参考，授权待核实`，只有取得覆盖POD用途的授权证据才更新。

看图并补充字段后生成图集：

```sh
python3 scripts/huaban_tools.py gallery /absolute/run/candidates.json --selected-only --output /absolute/run/gallery.html
```

`gallery.html` 展示保留项及理由；`candidates.json` 保留全部候选和来源；`report.md` 记录查询词、筛选取舍和缺口。没有可查看的图片时，不宣称初筛完成。
