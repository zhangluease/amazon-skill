#!/usr/bin/env python3
"""Repair an explicit watermark mask using clean pattern repeats or local OpenCV inpainting."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps


def period_offsets(periods, width, height):
    if not 1 <= len(periods) <= 2:
        raise ValueError("Repeat repair requires one or two measured --period dx,dy vectors")
    for dx, dy in periods:
        if (dx == dy == 0) or abs(dx) >= width or abs(dy) >= height:
            raise ValueError("A repeat vector must connect distinct pixels within the source")
    if len(periods) == 2 and periods[0][0] * periods[1][1] == periods[0][1] * periods[1][0]:
        raise ValueError("Two repeat vectors must point in different directions")
    (ax, ay), (bx, by) = periods if len(periods) == 2 else (periods[0], (0, 0))
    steps = range(-3, 4)
    offsets = {(a * ax + b * bx, a * ay + b * by) for a in steps
               for b in (steps if len(periods) == 2 else (0,))}
    offsets.discard((0, 0))
    return sorted(((dx, dy) for dx, dy in offsets if abs(dx) < width and abs(dy) < height),
                  key=lambda offset: (offset[0] ** 2 + offset[1] ** 2, offset))


def overlap_slices(shape, dx, dy):
    height, width = shape
    left, right = max(0, -dx), min(width, width - dx)
    top, bottom = max(0, -dy), min(height, height - dy)
    return (np.s_[top:bottom, left:right], np.s_[top + dy:bottom + dy, left + dx:right + dx])


def match_pattern(comparison, clean, details, target, donor):
    # Measure independent evidence in clean textured areas. Uniform background is
    # common to many wrong shifts, so a flat overlap alone does not verify a repeat.
    stride = max(1, int(math.sqrt(clean[target].size / 250_000)))
    sample = np.s_[::stride, ::stride]
    valid = clean[target][sample] & clean[donor][sample]
    textured = valid & (details[target][sample] | details[donor][sample])
    if np.count_nonzero(textured) < 24:
        return None
    a, b = comparison[target][sample], comparison[donor][sample]
    error = np.abs(a[valid].astype(np.int16) - b[valid]).max(axis=1)
    detail_error = error[textured[valid]]
    if np.mean(error <= 18) < 0.95 or np.mean(detail_error <= 18) < 0.90:
        return None
    return round(float(np.mean(detail_error <= 18)), 4)


def replace_from_repeats(original, masked, periods):
    result = original[:, :, :3].copy()
    known = ~masked
    remaining = masked.copy()
    kernel = np.ones((3, 3), np.uint8)
    safe = cv2.erode(known.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool) & (original[..., 3] > 0)
    rgb = original[:, :, :3]
    # Smooth only the comparison for lossy downloads. Donor pixels remain untouched.
    comparison = cv2.GaussianBlur(original, (5, 5), 0.8)
    details = cv2.morphologyEx(comparison[..., :3], cv2.MORPH_GRADIENT, kernel).max(axis=2) >= 12
    verified = []
    for dx, dy in period_offsets(periods, original.shape[1], original.shape[0]):
        target, donor = overlap_slices(masked.shape, dx, dy)
        usable = remaining[target] & known[donor] & (original[target][..., 3] == original[donor][..., 3])
        if not np.any(usable):
            continue
        agreement = match_pattern(comparison, safe, details, target, donor)
        if agreement is None:
            continue
        result[target][usable] = rgb[donor][usable]
        remaining[target][usable] = False
        verified.append({"dx": dx, "dy": dy, "detail_agreement": agreement,
                         "restored_pixels": int(np.count_nonzero(usable))})
        if not np.any(remaining):
            break
    return result, remaining, verified


def replace_flat_gaps(rgb, remaining, alpha, radius):
    """Inpaint small gaps surrounded by a flat, opaque border."""
    kernel = np.ones((5, 5), np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(remaining.astype(np.uint8), connectivity=8)
    eligible = np.zeros_like(remaining)
    height, width = remaining.shape
    for i in range(1, count):
        x, y, w, h, area = map(int, stats[i])
        if area > 4096 or x < 2 or y < 2 or x + w > width - 2 or y + h > height - 2:
            continue
        window = np.s_[y - 2:y + h + 2, x - 2:x + w + 2]
        pixels = labels[window] == i
        border = cv2.dilate(pixels.astype(np.uint8), kernel).astype(bool) & ~pixels
        if np.any(remaining[window][border]) or np.any(alpha[window][border | pixels] != 255):
            continue
        colors = rgb[window][border].astype(np.int16)
        if np.max(np.ptp(colors, axis=0)) <= 10:
            eligible[window] |= pixels
    if np.any(eligible):
        fill = cv2.inpaint(rgb, remaining.astype(np.uint8), radius, cv2.INPAINT_TELEA)
        rgb[eligible] = fill[eligible]
    return remaining & ~eligible, int(np.count_nonzero(eligible))


def repair(source, out, mask=None, boxes=(), radius=3, *, method="telea", periods=(), fill_flat=False):
    if not math.isfinite(radius) or not 0 < radius <= 30:
        raise ValueError("radius must be greater than 0 and at most 30")
    if not mask and not boxes:
        raise ValueError("Specify a visually checked mask or watermark rectangle")
    if method not in ("telea", "repeat") or (method == "telea" and (periods or fill_flat)):
        raise ValueError("Repeat vectors and --fill-flat are only supported with --method repeat")
    source, out = Path(source).resolve(), Path(out).resolve()
    output_names = ("source-clean.png", "source-review.png", "watermark-mask.png", "watermark-unresolved.png",
                    "watermark-before-after.jpg", "watermark-manifest.json")
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
    masked = mask_pixels > 0
    if method == "repeat":
        rgba = np.array(original)
        rgb, unresolved, offsets = replace_from_repeats(rgba, masked, periods)
        flat_count = 0
        if fill_flat and np.any(unresolved):
            unresolved, flat_count = replace_flat_gaps(rgb, unresolved, rgba[..., 3], radius)
        unresolved_count = int(np.count_nonzero(unresolved))
    else:
        rgb = np.array(original.convert("RGB"))
        reconstructed = cv2.inpaint(rgb, mask_pixels, radius, cv2.INPAINT_TELEA)
        rgb[masked] = reconstructed[masked]
        unresolved_count = 0
    result = Image.fromarray(rgb).convert("RGBA")
    result.putalpha(original.getchannel("A"))
    out.mkdir(parents=True, exist_ok=True)
    output = out / ("source-review.png" if unresolved_count else "source-clean.png")
    result.save(output)
    region.save(out / "watermark-mask.png")
    unresolved_path = out / "watermark-unresolved.png"
    if unresolved_count:
        Image.fromarray(unresolved.astype(np.uint8) * 255).save(unresolved_path)
    sheet = Image.new("RGB", (1000, 540), "#eeeeee")
    label = ImageDraw.Draw(sheet)
    review_title = (f"Partial repair / {unresolved_count:,} pixels unresolved" if unresolved_count
                    else "Repaired / visual review required")
    for index, (image, title) in enumerate(((original, "Original"), (result, review_title))):
        label.text((10 + index * 500, 10), title, fill="black")
        preview = ImageOps.contain(image, (480, 490), Image.Resampling.LANCZOS)
        sheet.paste(preview, (10 + index * 500, 35), preview)
    sheet.save(out / "watermark-before-after.jpg", quality=94)
    manifest = {"source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "file": str(output), "mask": str(out / "watermark-mask.png"),
                "width": original.width, "height": original.height,
                "method": "opencv_telea_explicit_mask", "radius": radius,
                "masked_fraction": fraction, "alpha_preserved": True,
                "status": "unresolved_regions" if unresolved_count else "pending_visual_review",
                "note": "Inspect the entire source and result at actual size for unmasked traces and altered pattern details."}
    if method == "repeat":
        manifest.update(method="aligned_repeat_explicit_mask", periods=periods, repeat_offsets=offsets,
                        repeat_restored_pixels=sum(offset["restored_pixels"] for offset in offsets),
                        flat_repaired_pixels=flat_count, unresolved_pixels=unresolved_count,
                        unresolved_mask=str(unresolved_path) if unresolved_count else None)
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


def period(value):
    try:
        dx, dy = (int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use dx,dy") from exc
    return dx, dy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mask", help="Black background with white watermark pixels, at source size")
    parser.add_argument("--rect", action="append", type=rectangle, default=[], help="x0,y0,x1,y1; repeatable")
    parser.add_argument("--method", choices=("telea", "repeat"), default="telea")
    parser.add_argument("--period", action="append", type=period, default=[], help="Measured repeat vector dx,dy; up to two")
    parser.add_argument("--fill-flat", action="store_true", help="Use Telea for small, verified flat gaps after repeat repair")
    parser.add_argument("--radius", type=float, default=3)
    args = parser.parse_args()
    try:
        result = repair(args.source, args.out, args.mask, args.rect, args.radius,
                        method=args.method, periods=args.period, fill_flat=args.fill_flat)
    except (ValueError, OSError, cv2.error) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["status"] == "unresolved_regions" else 0


if __name__ == "__main__":
    raise SystemExit(main())
