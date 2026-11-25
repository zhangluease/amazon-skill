#!/usr/bin/env python3
"""Repair a visually identified watermark region with OpenCV; retain the source and alpha."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps


def repair(source, out, mask=None, boxes=(), radius=3):
    if not math.isfinite(radius) or not 0 < radius <= 30:
        raise ValueError("radius must be greater than 0 and at most 30")
    if not mask and not boxes:
        raise ValueError("Specify a visually checked mask or watermark rectangle")
    source, out = Path(source).resolve(), Path(out).resolve()
    output_names = ("source-clean.png", "watermark-mask.png", "watermark-before-after.jpg",
                    "watermark-manifest.json")
    if source in [out / name for name in output_names]:
        raise ValueError("Output must not overwrite the source")
    if any((out / name).exists() for name in output_names):
        raise ValueError("Use a new output directory to preserve the previous repair")
    with Image.open(source) as opened:
        original = ImageOps.exif_transpose(opened).convert("RGBA")
    region = Image.new("L", original.size)
    if mask:
        with Image.open(mask) as opened:
            if opened.size != original.size:
                raise ValueError("Mask dimensions must match the EXIF-oriented source")
            region = opened.convert("L").point(lambda value: 255 if value > 127 else 0)
    draw = ImageDraw.Draw(region)
    for box in boxes:
        if len(box) != 4 or any(not isinstance(v, int) for v in box):
            raise ValueError("Rectangles require four integer pixel coordinates")
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= original.width and 0 <= y0 < y1 <= original.height):
            raise ValueError("Watermark rectangle must lie within the source")
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=255)
    mask_pixels = np.array(region)
    fraction = float(np.count_nonzero(mask_pixels)) / mask_pixels.size
    if not 0 < fraction < 1:
        raise ValueError("Mask must cover some, but not all, source pixels")
    rgb = np.array(original.convert("RGB"))
    reconstructed = cv2.inpaint(rgb, mask_pixels, radius, cv2.INPAINT_TELEA)
    # OpenCV must never modify pixels outside the explicitly selected region.
    rgb[mask_pixels > 0] = reconstructed[mask_pixels > 0]
    result = Image.fromarray(rgb).convert("RGBA")
    result.putalpha(original.getchannel("A"))
    out.mkdir(parents=True, exist_ok=True)
    result.save(out / "source-clean.png")
    region.save(out / "watermark-mask.png")
    sheet = Image.new("RGB", (1000, 540), "#eeeeee")
    label = ImageDraw.Draw(sheet)
    for index, (image, title) in enumerate(((original, "Original"), (result, "Repaired / visual review required"))):
        label.text((10 + index * 500, 10), title, fill="black")
        preview = ImageOps.contain(image, (480, 490), Image.Resampling.LANCZOS)
        sheet.paste(preview, (10 + index * 500, 35), preview)
    sheet.save(out / "watermark-before-after.jpg", quality=94)
    manifest = {"source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "file": str(out / "source-clean.png"), "mask": str(out / "watermark-mask.png"),
                "width": original.width, "height": original.height,
                "method": "opencv_telea_explicit_mask", "radius": radius,
                "masked_fraction": fraction, "alpha_preserved": True,
                "status": "pending_visual_review",
                "note": "Local reconstruction; not proof of watermark removal quality or POD rights."}
    (out / "watermark-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def rectangle(value):
    try:
        values = tuple(int(v) for v in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use x0,y0,x1,y1") from exc
    if len(values) != 4:
        raise argparse.ArgumentTypeError("Use x0,y0,x1,y1")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mask", help="Black background with white watermark pixels, at source size")
    parser.add_argument("--rect", action="append", type=rectangle, default=[], help="x0,y0,x1,y1; repeatable")
    parser.add_argument("--radius", type=float, default=3)
    args = parser.parse_args()
    try:
        result = repair(args.source, args.out, args.mask, args.rect, args.radius)
    except (ValueError, OSError, cv2.error) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
