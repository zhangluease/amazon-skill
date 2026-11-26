#!/usr/bin/env python3
"""Apply the requested width:height ratio to a complete set of exported effects."""
import argparse
from fractions import Fraction
import hashlib
import html
import json
from pathlib import Path
import re
from shutil import copyfile

from PIL import Image, ImageOps

from pod_image_size import MAX_PIXELS, MIN_SIDE, apply_detail_source, ceil_div


def parse_ratio(value):
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:：xX×/]\s*(\d+(?:\.\d+)?)\s*", str(value))
    if not match:
        raise ValueError("请填写宽:高比例，例如 1:1、4:5 或 16:9")
    width, height = (Fraction(part) for part in match.groups())
    if width <= 0 or height <= 0:
        raise ValueError("宽高比例必须为正数")
    ratio = width / height
    return ratio.numerator, ratio.denominator


def source_effects(manifest_file):
    manifest_file = Path(manifest_file).resolve()
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("原始效果图清单格式无效")
    effects = data.get("effects", [])
    expected = data.get("expected_view_ids", [])
    actual = [str(effect.get("view_id", "")) for effect in effects]
    if (data.get("status") != "complete" or data.get("missing_view_ids") or data.get("errors") or not effects
            or data.get("expected_count") != len(effects)
            or data.get("exported_count") != len(effects) or len(expected) != len(effects)
            or len(set(actual)) != len(actual) or set(map(str, expected)) != set(actual)
            or any(not re.fullmatch(r"[1-9]\d*", identifier) for identifier in actual)):
        raise ValueError("原始效果图未完整导出，不能生成全套最终图片")
    sources = []
    for effect in effects:
        source = (manifest_file.parent / effect["file"]).resolve()
        if not source.is_relative_to(manifest_file.parent):
            raise ValueError("原始效果图不在清单目录中")
        if effect.get("sha256") and hashlib.sha256(source.read_bytes()).hexdigest() != effect["sha256"]:
            raise ValueError(f"原始效果图已变化，请重新导出视角 {effect['view_id']}")
        with Image.open(source) as image, ImageOps.exif_transpose(image) as oriented:
            width, height = oriented.size
        sources.append((effect, source, width, height))
    return data, sources


def gallery(manifest):
    cards = []
    for effect in manifest["effects"]:
        name = html.escape(str(effect["name"]))
        file = effect["file"]
        cards.append(f'<figure><a href="{file}" target="_blank"><img src="{file}" alt="{name}"></a>'
                     f'<figcaption>{effect["index"]}. {name} · {effect["width"]} × {effect["height"]} '
                     f'<a href="{file}" download>下载</a></figcaption></figure>')
    ratio = manifest["output_aspect_ratio"]
    ratio_label = f"宽:高 {ratio}" if ratio else "宽高比见各张图片"
    size_label = (f'{manifest["width"]} × {manifest["height"]} px' if manifest["width"] is not None
                  else "像素尺寸见各张图片")
    status = "全部最终图片已生成。" if manifest["status"] == "complete" else "处理未完成，仍有缺失图片。"
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>最终产品效果图</title><style>body{font:16px system-ui;margin:24px;background:#f4f5f7;color:#222}'
            'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px}'
            'figure{margin:0;padding:12px;background:white;border-radius:8px}img{display:block;width:100%;height:auto}'
            'figcaption{padding-top:10px}a{color:#245dab}</style>'
            f'<h1>最终效果图 {manifest["exported_count"]}/{manifest["expected_count"]}</h1>'
            f'<p>{ratio_label} · {size_label} · {status}</p>'
            '<main>' + '\n'.join(cards) + '</main></html>')


def effect_sizes(width, height, ratio):
    if ratio is None and min(width, height) >= MIN_SIDE:
        return (width, height), (width, height)
    p, q = ratio or (1, 1)
    natural = max(Fraction(width, p), Fraction(height, q))
    units = max(ceil_div(width, p), ceil_div(height, q), ceil_div(MIN_SIDE, p), ceil_div(MIN_SIDE, q))
    canvas = p * units, q * units
    if min(width, height) >= MIN_SIDE or units == ceil_div(natural.numerator, natural.denominator):
        return canvas, (width, height)
    multiplier = units / natural
    return canvas, (round(width * multiplier), round(height * multiplier))


def format_ratio(size):
    ratio = Fraction(*size)
    return f"{ratio.numerator}:{ratio.denominator}"


def finalize(manifest_file, ratio, out, detail_sources=None):
    requested = parse_ratio(ratio) if ratio is not None else None
    data, sources = source_effects(manifest_file)
    out = Path(out).resolve()
    if out == Path(manifest_file).resolve().parent:
        raise ValueError("最终图片请使用独立目录，不能覆盖原始效果图")
    sizes = [effect_sizes(width, height, requested) for _, _, width, height in sources]
    detail_sources = detail_sources or {}
    enlarged_views = {str(effect["view_id"]) for (effect, _, width, height), (_, content) in zip(sources, sizes)
                      if content != (width, height)}
    if unknown := detail_sources.keys() - enlarged_views:
        raise ValueError(f"仅需实际放大的视角可使用超分候选：{', '.join(sorted(unknown))}")
    dimensions = {canvas for canvas, _ in sizes}
    ratios = {format_ratio(canvas) for canvas in dimensions}
    if any(canvas != (width, height) and canvas[0] * canvas[1] > MAX_PIXELS
           for (_, _, width, height), (canvas, _) in zip(sources, sizes)):
        raise ValueError("该比例会生成过大的画布，请使用 4:5、16:9 等简洁比例")
    common_width, common_height = next(iter(dimensions)) if len(dimensions) == 1 else (None, None)
    out.mkdir(parents=True, exist_ok=True)
    result = {"stage": "final", "status": "incomplete", "source_url": data.get("source_url"),
              "source_manifest": str(Path(manifest_file).resolve()), "requested_ratio": ratio,
              "design_fingerprint": data.get("design_fingerprint"),
              "output_aspect_ratio": next(iter(ratios)) if len(ratios) == 1 else None,
              "width": common_width, "height": common_height, "minimum_side": MIN_SIDE,
              "layout": "minimum_pixels_with_optional_aspect_ratio", "background": "#FFFFFF",
              "expected_count": len(sources), "expected_view_ids": [str(e[0]["view_id"]) for e in sources],
              "exported_count": 0, "missing_view_ids": [str(e[0]["view_id"]) for e in sources],
              "effects": [], "errors": []}
    manifest_path = out / "effects-manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "effects.html").write_text(gallery(result), encoding="utf-8")
    for index, ((effect, source, source_width, source_height), (canvas_size, content_size)) in enumerate(zip(sources, sizes), 1):
        identifier = str(effect["view_id"])
        width, height = canvas_size
        content_width, content_height = content_size
        x, y = (width - content_width) // 2, (height - content_height) // 2
        detail_source = detail_sources.get(identifier)
        detail = None
        upscaled = content_size != (source_width, source_height)
        try:
            with Image.open(source) as opened, ImageOps.exif_transpose(opened) as image:
                unchanged = (image.size == canvas_size == content_size and opened.getexif().get(274, 1) == 1
                             and opened.format in ("PNG", "JPEG"))
                extension = "jpg" if unchanged and opened.format == "JPEG" else "png"
                file = f"effect-{index:02d}-{identifier}.{extension}"
                target = out / file
                if unchanged:
                    copyfile(source, target)
                else:
                    content = image.convert("RGBA")
                    if upscaled:
                        content = content.resize(content_size, Image.Resampling.LANCZOS)
                    if detail_source is not None:
                        content, detail = apply_detail_source(content, detail_source)
                    canvas = Image.new("RGBA", canvas_size, "white")
                    canvas.alpha_composite(content, (x, y))
                    canvas.convert("RGB").save(target)
            entry = {"index": index, "view_id": identifier,
                "name": effect.get("name") or f"视角 {index}", "file": file,
                "width": width, "height": height, "source_file": str(source.resolve()),
                "source_width": source_width, "source_height": source_height,
                "aspect_ratio": format_ratio(canvas_size), "upscaled": upscaled,
                "resize_method": "lanczos" if upscaled else "original",
                "capture_method": effect.get("method"), "method": "original_copy" if unchanged else "ratio_canvas",
                "content_box": [x, y, x + content_width, y + content_height],
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
            if detail is not None:
                entry.update(resize_method="ai_super_resolution", detail_source=detail)
            result["effects"].append(entry)
        except (OSError, ValueError) as error:
            problem = {"view_id": identifier, "code": "EFFECT_RATIO_PROCESSING_FAILED"}
            if detail_source is not None:
                problem.update(code="EFFECT_DETAIL_SOURCE_FAILED", reason=str(error))
            result["errors"].append(problem)
    result["exported_count"] = len(result["effects"])
    result["missing_view_ids"] = [identifier for identifier in result["expected_view_ids"]
                                  if not any(e["view_id"] == identifier for e in result["effects"])]
    if not result["missing_view_ids"]:
        result["status"] = "complete"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "effects.html").write_text(gallery(result), encoding="utf-8")
    if result["status"] != "complete":
        raise ValueError("部分最终图片处理失败，请检查 effects-manifest.json 中的缺失视角")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Complete capture-stage effects-manifest.json")
    parser.add_argument("--ratio", help="Optional width:height. Undersized effects default to 1:1.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--detail-source", action="append", default=[], metavar="VIEW_ID=FILE",
                        help="Visually checked super-resolution candidate for a view that needs enlargement")
    args = parser.parse_args()
    try:
        detail_sources = {}
        for value in args.detail_source:
            view_id, _, source = value.partition("=")
            if not re.fullmatch(r"[1-9]\d*", view_id) or not source or view_id in detail_sources:
                raise ValueError("请为每个视角填写一个 --detail-source VIEW_ID=文件路径")
            detail_sources[view_id] = source
        result = finalize(args.manifest, args.ratio, args.out, detail_sources)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps({key: result[key] for key in (
        "status", "output_aspect_ratio", "width", "height", "expected_count", "exported_count")},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
