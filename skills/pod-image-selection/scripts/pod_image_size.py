#!/usr/bin/env python3
"""Bring a finished POD image up to the minimum resolution without reducing it."""
import argparse
import hashlib
import json
from pathlib import Path
from shutil import copyfile

from PIL import Image, ImageOps

MIN_SIDE = 2000
MAX_PIXELS = 100_000_000


def ceil_div(value, divisor):
    return (value + divisor - 1) // divisor


def ensure_minimum(image):
    short_side = min(image.size)
    if short_side >= MIN_SIDE:
        return image
    size = tuple(ceil_div(length * MIN_SIDE, short_side) for length in image.size)
    if size[0] * size[1] > MAX_PIXELS:
        raise ValueError("按原比例补足 2000 px 会生成过大的图像")
    if image.mode == "P":
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    elif image.mode == "1":
        image = image.convert("L")
    return image.resize(size, Image.Resampling.LANCZOS)


def apply_detail_source(reference, detail_source):
    """Fit a visually accepted super-resolution candidate to the planned pixels."""
    candidate = Path(detail_source).resolve()
    width, height = reference.size
    with Image.open(candidate) as opened, ImageOps.exif_transpose(opened) as image:
        cw, ch = image.size
        if cw < width or ch < height:
            raise ValueError("超分候选像素小于目标尺寸")
        # Allow integer rounding at the two image sizes, but reject a change of framing.
        if abs(cw * height - ch * width) > max(cw, ch):
            raise ValueError("超分候选的宽高比与原图不一致")
        pixels = image.convert("RGB")
        if pixels.size != reference.size:
            pixels = pixels.resize(reference.size, Image.Resampling.LANCZOS)
    if "A" in reference.getbands() or "transparency" in reference.info:
        pixels.putalpha(reference.convert("RGBA").getchannel("A"))
    return pixels, {"file": str(candidate), "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()}


def size_file(source, out, detail_source=None):
    source, out = Path(source).resolve(), Path(out).resolve()
    if source == out or out.exists():
        raise ValueError("请指定一个新文件保留原图")
    out.parent.mkdir(parents=True, exist_ok=True)
    detail = None
    with Image.open(source) as opened, ImageOps.exif_transpose(opened) as original:
        target = ensure_minimum(original)
        changed = target is not original
        size = target.size
        if detail_source is not None:
            if not changed:
                raise ValueError("原图像素已达标，保留原图")
            target, detail = apply_detail_source(target, detail_source)
        if changed:
            if out.suffix.lower() != ".png":
                raise ValueError("放大后的图片请输出 PNG")
            if target.mode in ("CMYK", "YCbCr", "HSV"):
                target = target.convert("RGB")
            target.save(out)
        else:
            if Image.registered_extensions().get(out.suffix.lower()) != opened.format:
                raise ValueError("已达标图片请沿用原文件格式")
            copyfile(source, out)
    result = {"file": str(out), "width": size[0], "height": size[1], "minimum_side": MIN_SIDE,
              "upscaled": changed, "resize_method": "lanczos" if changed else "original"}
    if detail is not None:
        result.update(resize_method="ai_super_resolution", detail_source=detail)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--detail-source", help="Visually checked super-resolution candidate at or above the target size")
    args = parser.parse_args()
    try:
        print(json.dumps(size_file(args.source, args.out, args.detail_source), ensure_ascii=False))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
