#!/usr/bin/env python3
"""Prepare and composite an AI watermark repair at the original pixel size."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps


def read_image(file, mode="RGBA"):
    with Image.open(file) as image:
        return np.array(ImageOps.exif_transpose(image).convert(mode))


def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def write_json(file, value):
    file.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def phase_indices(image_shape, tile):
    x, y, width, height = tile
    return (np.arange(image_shape[0]) - y) % height, (np.arange(image_shape[1]) - x) % width


def assemble_tile(source, mask, tile):
    x, y, width, height = tile
    unit = source[y:y + height, x:x + width].copy()
    needed = mask[y:y + height, x:x + width].copy()
    ys, xs = phase_indices(mask.shape, tile)
    # In each repeat, reuse any clean original pixels before asking AI to infer them.
    for top in range(y % height - height, mask.shape[0], height):
        for left in range(x % width - width, mask.shape[1], width):
            y0, y1 = max(0, top), min(mask.shape[0], top + height)
            x0, x1 = max(0, left), min(mask.shape[1], left + width)
            if y0 >= y1 or x0 >= x1:
                continue
            target = np.ix_(ys[y0:y1], xs[x0:x1])
            region = np.s_[y0:y1, x0:x1]
            accept = needed[target] & ~mask[region] & (unit[target][..., 3] == source[region][..., 3])
            unit[target] = np.where(accept[..., None], source[region], unit[target])
            needed[target] &= ~accept
    return unit, needed


def prepare(source, mask, out, tile=None, context=0):
    source, mask, out = [Path(p).resolve() for p in (source, mask, out)]
    rgba, full_mask = read_image(source), read_image(mask, "L") > 127
    height, width = rgba.shape[:2]
    if full_mask.shape != (height, width) or not np.any(full_mask) or np.all(full_mask):
        raise ValueError("Use a partial, visually checked binary mask at the original image size")
    if tile:
        x, y, w, h = tile
        if not (0 <= x < x + w <= width and 0 <= y < y + h <= height and (w, h) != (width, height)):
            raise ValueError("Choose a measured full repeat within the source")
        if not 0 <= context <= min(w, h) // 2:
            raise ValueError("Context must be between zero and half the short side of the repeat")
        unit, needed = assemble_tile(rgba, full_mask, tile)
        frame = np.pad(unit, ((context, context), (context, context), (0, 0)), mode="wrap")
        frame_mask = np.pad(needed, context, mode="wrap")
    else:
        if context:
            raise ValueError("Wraparound context requires a measured repeat")
        frame, frame_mask = rgba, full_mask
    if np.all(frame_mask):
        raise ValueError("AI needs some clear context around the requested repair")
    out.mkdir(parents=True, exist_ok=True)
    plan_path = out / "ai-repair-plan.json"
    if plan_path.exists():
        raise ValueError("Use a new directory for a new repair plan")
    frame_path, mask_path, guide_path = (out / name for name in ("ai-input.png", "ai-mask.png", "ai-guide.png"))
    Image.fromarray(frame).save(frame_path)
    Image.fromarray(frame_mask.astype(np.uint8) * 255).save(mask_path)
    guide = frame.copy()
    guide[frame_mask] = (255, 0, 220, 255)
    Image.fromarray(guide).save(guide_path)
    plan = {"source": str(source), "source_sha256": digest(source), "full_mask": str(mask),
            "full_mask_sha256": digest(mask), "width": width, "height": height,
            "tile": tile, "context": context, "input": str(frame_path), "input_sha256": digest(frame_path),
            "ai_mask": str(mask_path), "ai_mask_sha256": digest(mask_path), "guide": str(guide_path),
            "ai_pixels_in_frame": int(np.count_nonzero(frame_mask))}
    write_json(plan_path, plan)
    return plan


def compare_frame(source, candidate, mask):
    a, b = (cv2.GaussianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (5, 5), 0.8)
            for rgb in (source, candidate))
    excluded = cv2.dilate(mask.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    edge = cv2.morphologyEx(a, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) >= 12
    detail = edge & ~excluded
    known = ~excluded
    error = np.abs(a.astype(np.int16) - b)
    return {"known_median_error": round(float(np.median(error[known])), 2) if np.any(known) else 255.0,
            "detail_agreement": round(float(np.mean(error[detail] <= 25)), 4) if np.any(detail) else None}


def register(source, candidate, mask):
    height, width = source.shape[:2]
    ch, cw = candidate.shape[:2]
    if cw < width or ch < height or abs(cw / ch / (width / height) - 1) > 0.015:
        raise ValueError("AI output must cover the same frame and aspect ratio as ai-input.png")
    candidate = cv2.resize(candidate, (width, height), interpolation=cv2.INTER_AREA)
    scores = compare_frame(source, candidate, mask)
    if scores["detail_agreement"] is None or scores["detail_agreement"] >= 0.95:
        return candidate, scores, None
    # Estimate small framing differences from unmasked artwork only.
    outside = (mask == 0).astype(np.uint8) * 255
    a, b = (cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) for rgb in (source, candidate))
    matrix = np.eye(2, 3, dtype=np.float32)
    try:
        _, matrix = cv2.findTransformECC(a, b, matrix, cv2.MOTION_AFFINE,
                                         (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5), outside, 5)
    except cv2.error:
        return candidate, scores, None
    corners = np.float32([[0, 0], [width - 1, 0], [0, height - 1], [width - 1, height - 1]])
    movement = np.linalg.norm(corners @ matrix[:, :2].T + matrix[:, 2] - corners, axis=1)
    if movement.max() > min(width, height) * 0.04:
        return candidate, scores, None
    aligned = cv2.warpAffine(candidate, matrix, (width, height), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                             borderMode=cv2.BORDER_REFLECT_101)
    aligned_scores = compare_frame(source, aligned, mask)
    if aligned_scores["detail_agreement"] > scores["detail_agreement"]:
        return aligned, aligned_scores, matrix.tolist()
    return candidate, scores, None


def composite(plan_file, candidate_file, out):
    plan_file, candidate_file, out = [Path(p).resolve() for p in (plan_file, candidate_file, out)]
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    for key in ("source", "full_mask", "input", "ai_mask"):
        if digest(Path(plan[key])) != plan[key + "_sha256"]:
            raise ValueError(f"{key} changed after AI repair preparation")
    native = read_image(plan["source"])
    full_mask = read_image(plan["full_mask"], "L") > 127
    frame, mask = read_image(plan["input"]), read_image(plan["ai_mask"], "L") > 127
    candidate = read_image(candidate_file, "RGB")
    aligned, scores, affine = register(frame[..., :3], candidate, mask)
    manifest_path = out / "watermark-ai-manifest.json"
    if manifest_path.exists():
        raise ValueError("Use a new directory for a new candidate")
    out.mkdir(parents=True, exist_ok=True)
    result = {"status": "alignment_rejected", "source": plan["source"], "plan": str(plan_file),
              "candidate": str(candidate_file), "candidate_sha256": digest(candidate_file),
              "width": native.shape[1], "height": native.shape[0], "alignment": scores,
              "affine": affine, "output": None}
    agreement = scores["detail_agreement"]
    if scores["known_median_error"] > 20 or (agreement is not None and agreement < 0.72):
        write_json(manifest_path, result)
        return result
    frame[mask, :3] = aligned[mask]
    review_path = out / "source-ai-review.png"
    if plan["tile"]:
        x, y, w, h = tile = plan["tile"]
        pad = plan["context"]
        unit = frame[pad:pad + h, pad:pad + w]
        ys, xs = phase_indices(full_mask.shape, tile)
        replacement = unit[np.ix_(ys, xs)]
        Image.fromarray(unit).save(out / "review-tile.png")
        Image.fromarray(np.tile(unit, (2, 2, 1))).save(out / "review-repeat-2x2.png")
    else:
        replacement = frame
    native[full_mask, :3] = replacement[full_mask, :3]
    Image.fromarray(native).save(review_path)
    result.update(status="pending_visual_review", output=str(review_path),
                  original_pixels_preserved_outside_mask=True, alpha_preserved=True,
                  masked_pixels=int(np.count_nonzero(full_mask)))
    write_json(manifest_path, result)
    return result


def parse_tile(value):
    try:
        x, y, w, h = map(int, value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use x,y,width,height") from exc
    return x, y, w, h


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("prepare")
    start.add_argument("--source", required=True)
    start.add_argument("--mask", required=True)
    start.add_argument("--out", required=True)
    start.add_argument("--tile", type=parse_tile)
    start.add_argument("--context", type=int, default=0)
    finish = commands.add_parser("composite")
    finish.add_argument("--plan", required=True)
    finish.add_argument("--candidate", required=True)
    finish.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.source, args.mask, args.out, args.tile, args.context)
        else:
            result = composite(args.plan, args.candidate, args.out)
    except (OSError, ValueError, cv2.error, KeyError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("status") == "alignment_rejected" else 0


if __name__ == "__main__":
    raise SystemExit(main())
