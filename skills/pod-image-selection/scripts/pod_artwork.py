#!/usr/bin/env python3
"""Prepare artwork for every editable POD surface (Pillow required)."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps


def white_mask(image, tolerance=20):
    rgb = image.convert("RGB")
    channels = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white")).split()
    distance = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    return distance.point(lambda value: 255 if value <= tolerance else 0)


def fraction(mask):
    return mask.histogram()[255] / (mask.width * mask.height)


def analyze(image, tolerance=20, blank_threshold=0.08):
    sample = image.convert("RGBA")
    sample.thumbnail((768, 768))
    alpha = sample.getchannel("A")
    transparent = alpha.point(lambda value: 255 if value <= 8 else 0)
    visible_white = ImageChops.multiply(white_mask(sample, tolerance),
                                       alpha.point(lambda value: 255 if value > 8 else 0))
    blank = ImageChops.lighter(transparent, visible_white)
    ratio = fraction(blank)
    return {
        "blank_ratio": round(ratio, 6),
        "transparent_ratio": round(fraction(transparent), 6),
        "has_transparency": alpha.getextrema()[0] < 255,
        "classification": "non_full_print" if ratio > blank_threshold else "full_print",
        "classification_method": "transparent_or_near_white_pixel_ratio",
        "blank_threshold": blank_threshold,
        "white_tolerance": tolerance,
        "classification_note": "Heuristic: white may be intentional artwork; review the preview.",
    }


def remove_edge_white(image, tolerance=20):
    """Only remove near-white regions connected to a canvas edge; retain enclosed whites."""
    rgba = image.convert("RGBA")
    mask = white_mask(rgba, tolerance)
    width, height = mask.size
    boundary = [(x, y) for x in range(width) for y in (0, height - 1)]
    boundary += [(x, y) for y in range(height) for x in (0, width - 1)]
    for point in boundary:
        if mask.getpixel(point) == 255:
            ImageDraw.floodfill(mask, point, 128, thresh=0)
    keep = mask.point(lambda value: 0 if value == 128 else 255)
    rgba.putalpha(ImageChops.multiply(rgba.getchannel("A"), keep))
    return rgba


def editable_surfaces(template):
    surfaces = []
    for surface in template.get("data", []):
        config = surface.get("config", {})
        if not config.get("edit"):
            continue
        width, height = int(config["flatWidth"]), int(config["flatHeight"])
        if width <= 0 or height <= 0 or width * height > 100_000_000:
            raise ValueError("Invalid or excessively large surface dimensions")
        surfaces.append({"id": surface["id"], "name": surface["name"],
                         "width": width, "height": height, "svg_url": surface.get("svgUrl")})
    if not surfaces:
        raise ValueError("Template has no editable design surfaces")
    return surfaces


def checkerboard(size, cell=20):
    image = Image.new("RGB", size, "#f2f2f2")
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill="#d8d8d8")
    return image


def prepare(source, template, out, fit="cover", background="auto", tolerance=20,
            blank_threshold=0.08):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as loaded:
        source_mode = loaded.mode
        original = ImageOps.exif_transpose(loaded).convert("RGBA")
    before = analyze(original, tolerance, blank_threshold)
    results = []
    prepared_sizes = {}
    for surface in editable_surfaces(template):
        size = (surface["width"], surface["height"])
        if size not in prepared_sizes:
            if fit == "cover":
                result = ImageOps.fit(original, size, method=Image.Resampling.LANCZOS)
            else:
                result = ImageOps.contain(original, size, method=Image.Resampling.LANCZOS)
            standardized = analyze(result, tolerance, blank_threshold)
            cutout = background == "remove-white" or (background == "auto" and
                     standardized["classification"] == "non_full_print" and
                     not standardized["has_transparency"])
            result = remove_edge_white(result, tolerance) if cutout else result
            if fit == "contain":
                padded = Image.new("RGBA", size)
                padded.alpha_composite(result, ((size[0] - result.width) // 2,
                                                (size[1] - result.height) // 2))
                result = padded
            prepared_sizes[size] = (result, standardized, cutout)
        result, standardized, cutout = prepared_sizes[size]
        target = out / ("flat-%s.png" % surface["id"])
        result.save(target)
        results.append(dict(surface, file=str(target.resolve()), mode=result.mode,
                            upscaled=(max(size[0] / original.width, size[1] / original.height)
                                      if fit == "cover" else
                                      min(size[0] / original.width, size[1] / original.height)) > 1,
                            standardized_analysis=standardized,
                            background_action="remove_edge_connected_white" if cutout else "preserve",
                            analysis=analyze(result, tolerance, blank_threshold)))
    # A before/after image that makes actual transparency visible.
    sheet = Image.new("RGB", (1000, 545), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((15, 12), "Original / %s x %s" % original.size, fill="black")
    draw.text((515, 12), "Processed / checkerboard = transparency", fill="black")
    left = ImageOps.contain(original, (480, 480), Image.Resampling.LANCZOS)
    sheet.paste(left, (10, 45), left)
    right = checkerboard((480, 480))
    with Image.open(results[0]["file"]) as prepared:
        tile = ImageOps.contain(prepared, (480, 480), Image.Resampling.LANCZOS)
        right.paste(tile, ((480 - tile.width) // 2, (480 - tile.height) // 2), tile)
    sheet.paste(right, (510, 45))
    sheet.save(out / "artwork-before-after.jpg", quality=94)
    manifest = {"source": str(Path(source).resolve()), "source_size": list(original.size),
                "source_mode": source_mode,
                "source_analysis": before, "fit": fit,
                "surfaces": results, "status": "pending_user_confirmation"}
    (out / "artwork-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--template", required=True, help="Decoded podqhy flatList JSON")
    parser.add_argument("--out", required=True)
    parser.add_argument("--fit", choices=("cover", "contain"), default="cover")
    parser.add_argument("--background", choices=("auto", "preserve", "remove-white"), default="auto")
    parser.add_argument("--white-tolerance", type=int, default=20)
    parser.add_argument("--blank-threshold", type=float, default=0.08)
    args = parser.parse_args()
    if not 0 <= args.white_tolerance <= 255 or not 0 <= args.blank_threshold <= 1:
        parser.error("Tolerance must be 0–255 and blank threshold must be 0–1")
    manifest = prepare(args.source, json.loads(Path(args.template).read_text()), args.out,
                       args.fit, args.background, args.white_tolerance, args.blank_threshold)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
