#!/usr/bin/env python3
"""Apply the requested width:height ratio to a complete set of exported effects."""
import argparse
from fractions import Fraction
import hashlib
import html
import json
from pathlib import Path
import re

from PIL import Image, ImageOps

MAX_PIXELS = 100_000_000


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
    status = "全部最终图片已生成。" if manifest["status"] == "complete" else "处理未完成，仍有缺失图片。"
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>最终产品效果图</title><style>body{font:16px system-ui;margin:24px;background:#f4f5f7;color:#222}'
            'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px}'
            'figure{margin:0;padding:12px;background:white;border-radius:8px}img{display:block;width:100%;height:auto}'
            'figcaption{padding-top:10px}a{color:#245dab}</style>'
            f'<h1>最终效果图 {manifest["exported_count"]}/{manifest["expected_count"]}</h1>'
            f'<p>宽:高 {ratio} · {manifest["width"]} × {manifest["height"]} px · {status}</p>'
            '<main>' + '\n'.join(cards) + '</main></html>')


def finalize(manifest_file, ratio, out):
    p, q = parse_ratio(ratio)
    data, sources = source_effects(manifest_file)
    out = Path(out).resolve()
    if out == Path(manifest_file).resolve().parent:
        raise ValueError("最终图片请使用独立目录，不能覆盖原始效果图")
    max_width = max(source[2] for source in sources)
    max_height = max(source[3] for source in sources)
    # Smallest common canvas with the exact requested ratio that contains every original.
    scale = max((max_width + p - 1) // p, (max_height + q - 1) // q)
    width, height = p * scale, q * scale
    if width * height > MAX_PIXELS:
        raise ValueError("该比例会生成过大的画布，请使用 4:5、16:9 等简洁比例")
    out.mkdir(parents=True, exist_ok=True)
    result = {"stage": "final", "status": "incomplete", "source_url": data.get("source_url"),
              "source_manifest": str(Path(manifest_file).resolve()), "requested_ratio": str(ratio),
              "design_fingerprint": data.get("design_fingerprint"),
              "output_aspect_ratio": f"{p}:{q}", "width": width, "height": height,
              "layout": "contain_original_with_white_padding", "background": "#FFFFFF",
              "expected_count": len(sources), "expected_view_ids": [str(e[0]["view_id"]) for e in sources],
              "exported_count": 0, "missing_view_ids": [str(e[0]["view_id"]) for e in sources],
              "effects": [], "errors": []}
    manifest_path = out / "effects-manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "effects.html").write_text(gallery(result), encoding="utf-8")
    for index, (effect, source, source_width, source_height) in enumerate(sources, 1):
        identifier = str(effect["view_id"])
        try:
            with Image.open(source) as opened, ImageOps.exif_transpose(opened) as image:
                canvas = Image.new("RGBA", (width, height), "white")
                x, y = (width - source_width) // 2, (height - source_height) // 2
                canvas.alpha_composite(image.convert("RGBA"), (x, y))
                file = f"effect-{index:02d}-{identifier}.png"
                target = out / file
                canvas.convert("RGB").save(target)
            result["effects"].append({"index": index, "view_id": identifier,
                "name": effect.get("name") or f"视角 {index}", "file": file,
                "width": width, "height": height, "source_file": str(source.resolve()),
                "source_width": source_width, "source_height": source_height,
                "capture_method": effect.get("method"), "method": "ratio_canvas",
                "content_box": [x, y, x + source_width, y + source_height],
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        except (OSError, ValueError):
            result["errors"].append({"view_id": identifier, "code": "EFFECT_RATIO_PROCESSING_FAILED"})
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
    parser.add_argument("--ratio", required=True, help="Required width:height, e.g. 1:1, 4:5, 16:9")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        result = finalize(args.manifest, args.ratio, args.out)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps({key: result[key] for key in (
        "status", "output_aspect_ratio", "width", "height", "expected_count", "exported_count")},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
