---
name: pod-image-selection
description: 为POD选图。按关键词获取Amazon美国站搜索结果，用卖家精灵查询父体销量和主图，降序展示全部有效候选供用户多选；以所选主图在花瓣以图搜图，再看图初筛参考素材。
---

# POD选图

输入关键词，例如 `pet house`。固定美国站，默认Amazon搜索第一页，配送目的地（Deliver to）默认按国家选择美国，不指定邮编。使用 `--amazon-country JP` 可改为配送到日本；需要具体美国地区时另加 `--amazon-zip`。配送国家与搜索站点独立，选择日本仍搜索Amazon.com。所选图片决定后续素材搜索的主题和风格。

## 1. 查询竞品并等待选图

在skill目录运行，脚本和输出路径按实际位置填写：

```sh
node scripts/sellersprite_browser.cjs search --query 'pet house' --out /absolute/run
```

入口自动处理环境与会话，按“Amazon匿名HTTP搜索ASIN→卖家精灵批量查销量/主图”执行，按父体近30天销量降序展示全部有效候选。

展示 `products.html`，用户可回复编号或导出选择JSON。将选中编号与ASIN保存为 `selected-products.json`，**等待用户选择后再继续**。

分页、候选范围与错误处理见 [竞品查询](references/sellersprite-access.md)；授权或环境问题见 [环境与人工接管](references/environment.md)。

## 2. 在花瓣以图搜图并筛选

按已选ASIN从排序数据取对应主图，逐张原样提交花瓣“以图搜索”。每张检索完成后随机冷却4–6秒，再提交下一张；入口在连续命令间共享频控并串行执行图片搜索。记录参考图、来源ASIN、图片摘要和实际结果页；多选时分别检索，再合并去重。具体命令及花瓣会话要求见 [花瓣检索与初筛](references/huaban-workflow.md)。

实际看图后，按主体、绘画风格、配色、构图和装饰元素筛选，区分图案与商品外形、商标、文字、包装及摄影背景。可从合适结果继续扩展相似图和推荐画板，保留来源、筛选理由和授权状态。用户明确要求关键词补搜时才提取搜索词；默认流程无需先生成 `visual-elements.json`。

用户直接提供参考图或明确只找素材时，从本阶段开始。

## 交付

结果放在当前项目 `reports/pod-selection/<任务>/`。竞品阶段交付 `products.html` 和排序数据；素材阶段交付 `gallery.html`、`candidates.json`、`report.md`。仅生成本轮已完成阶段的文件。参考素材默认标记“灵感参考，授权待核实”。
