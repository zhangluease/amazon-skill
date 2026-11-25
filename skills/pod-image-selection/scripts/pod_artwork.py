#!/usr/bin/env python3
"""Prepare artwork for every editable POD surface (Pillow required)."""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

UPLOAD_MAX_BYTES = 30 * 1024 * 1024


def white_mask(image, tolerance=20):
    rgb = image.convert("RGB")
    channels = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white")).split()
    distance = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    return distance.point(lambda value: 255 if value <= tolerance else 0)


def fraction(mask):
    return mask.histogram()[255] / (mask.width * mask.height)


def analyze(image, tolerance=20, blank_threshold=0.08):
    sample = image.convert("RGBA")
    has_transparency = sample.getchannel("A").getextrema()[0] < 255
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
        "has_transparency": has_transparency,
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


def encode_upload(image, max_bytes):
    """Keep full size if possible and preserve alpha when reducing file size."""
    def encode(candidate, kind, **options):
        stream = io.BytesIO()
        candidate.save(stream, format=kind, **options)
        return stream.getvalue()

    opaque = image.getchannel("A").getextrema()[0] == 255
    candidate = image
    scale = 1.0
    while True:
        png_image = candidate.convert("RGB") if opaque else candidate
        payload = encode(png_image, "PNG")
        if len(payload) <= max_bytes:
            return candidate, payload, "png"
        if opaque:
            payload = encode(png_image, "JPEG", quality=95, subsampling=0, optimize=True)
            if len(payload) <= max_bytes:
                with Image.open(io.BytesIO(payload)) as uploaded:
                    return uploaded.convert("RGBA"), payload, "jpg"
        if candidate.size == (1, 1):
            raise ValueError("Upload file cannot fit the PODQHY file size limit")
        scale *= min(0.9, math.sqrt(max_bytes / len(payload)) * 0.95)
        bounds = tuple(max(1, int(length * scale)) for length in image.size)
        # Resize from the original at every step to avoid repeated resampling.
        candidate = image.resize(bounds, Image.Resampling.LANCZOS)


def prepare(source, template, out, fit="cover", background="auto", tolerance=20,
            blank_threshold=0.08):
    surfaces = editable_surfaces(template)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as loaded:
        source_mode = loaded.mode
        original = ImageOps.exif_transpose(loaded).convert("RGBA")
    before = analyze(original, tolerance, blank_threshold)
    # Keep the original aspect ratio for one reusable site upload. Cropping this
    # image to the first face would discard artwork needed by differently shaped faces.
    source_cutout = background == "remove-white" or (background == "auto" and
                    before["classification"] == "non_full_print" and
                    not before["has_transparency"])
    artwork = remove_edge_white(original, tolerance) if source_cutout else original
    upload_image, payload, extension = encode_upload(artwork, UPLOAD_MAX_BYTES)
    upload_sha256 = hashlib.sha256(payload).hexdigest()
    upload_file = out / f"upload-artwork-{upload_sha256[:16]}.{extension}"
    upload_file.write_bytes(payload)
    results = []
    prepared_sizes = {}
    resize = ImageOps.fit if fit == "cover" else ImageOps.contain
    for surface in surfaces:
        size = (surface["width"], surface["height"])
        if size not in prepared_sizes:
            fitted_source = resize(original, size, method=Image.Resampling.LANCZOS)
            standardized = analyze(fitted_source, tolerance, blank_threshold)
            result = fitted_source if upload_image is original else resize(
                upload_image, size, method=Image.Resampling.LANCZOS)
            cutout = source_cutout and bool(ImageChops.difference(
                fitted_source.getchannel("A"), result.getchannel("A")).getbbox())
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
                            upscaled=(max(size[0] / upload_image.width, size[1] / upload_image.height)
                                      if fit == "cover" else
                                      min(size[0] / upload_image.width, size[1] / upload_image.height)) > 1,
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
                "upload_plan": {"strategy": "upload_once_copy_all",
                    "file": str(upload_file.resolve()),
                    "sha256": upload_sha256, "size_bytes": len(payload),
                    "resized_for_upload": upload_image.size != artwork.size,
                    "width": upload_image.width, "height": upload_image.height,
                    "background_action": "remove_edge_connected_white" if source_cutout else "preserve",
                    "source_surface_id": results[0]["id"],
                    "surface_ids": [surface["id"] for surface in results],
                    "copy_position": "center", "per_surface_placement": fit,
                    "requires_surface_review": True},
                "surfaces": results, "status": "prepared_pending_surface_review"}
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
