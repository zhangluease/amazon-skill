---
name: pod-image-selection
description: 为POD选图。按关键词获取Amazon美国站搜索结果，用卖家精灵查询父体销量和主图，降序展示全部有效候选供用户多选；提取所选图片的视觉元素，再到花瓣检索并初筛参考素材。
---

# POD选图

输入关键词，例如 `pet house`。固定美国站，默认Amazon搜索第一页；所选图片决定后续素材搜索的主题和风格。

## 1. 查询竞品并等待选图

在skill目录运行，脚本和输出路径按实际位置填写：

```sh
node scripts/sellersprite_browser.cjs search --query 'pet house' --out /absolute/run
```

入口自动处理环境与会话，按“Amazon匿名HTTP搜索ASIN→卖家精灵批量查销量/主图”执行，按父体近30天销量降序展示全部有效候选。

展示 `products.html`，用户可回复编号或导出选择JSON。将选中编号与ASIN保存为 `selected-products.json`，**等待用户选择后再继续**。

分页、候选范围与错误处理见 [竞品查询](references/sellersprite-access.md)；授权或环境问题见 [环境与人工接管](references/environment.md)。

## 2. 提取视觉元素并找参考图

逐张查看已选主图，提取主体、绘画风格、配色、构图和装饰元素，记录来源ASIN到 `visual-elements.json`。区分图案与商品外形、商标、文字、包装及摄影背景；多张图片风格不同时分组检索。

按 [花瓣检索与初筛](references/huaban-workflow.md) 搜索、扩展相似图和推荐画板。实际看图后去重，保留来源、筛选理由和授权状态。

用户直接提供参考图或明确只找素材时，从本阶段开始。

## 交付

结果放在当前项目 `reports/pod-selection/<任务>/`。竞品阶段交付 `products.html` 和排序数据；素材阶段交付 `gallery.html`、`candidates.json`、`report.md`。仅生成本轮已完成阶段的文件。参考素材默认标记“灵感参考，授权待核实”。
