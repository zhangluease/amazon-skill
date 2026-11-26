# AI 局部补修

先用确定性方法保住原图细节。重复纹样优先找同位的干净像素，只有残余覆盖才交给 AI。待修图优先用 `source-review.png`，把 `watermark-unresolved.png` 与实际原尺寸图叠看，补标斜线、断在边缘的标识和修补残影。预处理损坏图案时，用原图与完整水印掩码重做。保留原始下载文件和两步掩码。

## 准备一份 AI 输入

矩形平铺时，选择一块实测的完整单元，给 AI 加少量环绕上下文，让同一图案只修一次。图案无可靠周期或无法用单个矩形表示时，省略 `--tile` 和 `--context`，以原图构图修补。坐标、周期和边缘上下文由当前图测量；示例尺寸用于演示参数。

```sh
uv run --with 'pillow>=10,<12' --with 'opencv-python-headless>=4.10,<5' \
  python scripts/pod_watermark_ai.py prepare \
  --source /absolute/run/cleaned/source-review.png \
  --mask /absolute/run/reviewed-residual-mask.png \
  --tile 75,53,329,329 --context 24 --out /absolute/run/ai-repair
```

`ai-input.png` 为待修图，`ai-mask.png` 的白色为允许改动区域，`ai-guide.png` 以亮洋红标出同一区域。脚本会从同相位的干净像素先补回单元中的细节，减少交给 AI 推测的内容。若 `ai_pixels_in_frame` 为零，可直接将 `ai-input.png` 用作下一步的候选。

有原生掩码参数的图像编辑工具用 `ai-input.png` 与 `ai-mask.png`。使用内置图像编辑工具时，将 `ai-guide.png` 作为编辑目标、`ai-input.png` 作为同位置的内容参考。要求仅去掉标色区内的水印，延续原有背景和线条，保持边到边构图、相同位置、比例和方位。只返回一张修补后的图。保留提示词与实际候选文件。

## 贴回原尺寸并检查

```sh
uv run --with 'pillow>=10,<12' --with 'opencv-python-headless>=4.10,<5' \
  python scripts/pod_watermark_ai.py composite \
  --plan /absolute/run/ai-repair/ai-repair-plan.json \
  --candidate /absolute/run/ai-candidate.png --out /absolute/run/ai-result
```

脚本先用掩码外的图案检查候选对齐情况，再只取掩码内的补片，输出与输入像素尺寸一致的 `source-ai-review.png`。模型输出尺寸若较大，仅在合成时映射回原尺寸。小幅偏差可以对齐，明显变形或移位返回 `alignment_rejected` 并留下核验数据；换一张构图稳定的候选或缩小修补范围后重试。重复纹样另有 `review-tile.png` 和 `review-repeat-2x2.png` 供检查接缝。

`pending_visual_review` 需要查看全图、原尺度补片和横纵接缝，确认没有残留文字、断裂或异色块。合成仅保护掩码外的像素，掩码里的 AI 笔画仍需复核。通过后把实际文件和 `watermark_review: passed` 写入任务记录，再走尺寸适配与上传。
