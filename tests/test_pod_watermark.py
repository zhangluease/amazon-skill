import importlib.util
from pathlib import Path
import tempfile
import unittest

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
except ImportError:
    cv2 = None

SCRIPT = Path(__file__).resolve().parents[1] / "skills/pod-image-selection/scripts/pod_watermark.py"
if cv2 is not None:
    spec = importlib.util.spec_from_file_location("pod_watermark", SCRIPT)
    watermark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watermark)


@unittest.skipIf(cv2 is None, "Run with Pillow and opencv-python-headless")
class WatermarkTests(unittest.TestCase):
    def test_explicit_region_repaired_without_changing_other_pixels_or_alpha(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            image = Image.new("RGBA", (96, 60), (20, 90, 180, 160))
            ImageDraw.Draw(image).rectangle((40, 20, 49, 29), fill=(255, 255, 255, 110))
            image.save(source)
            original_bytes = source.read_bytes()
            result = watermark.repair(source, root / "clean", boxes=[(38, 18, 52, 32)])
            with Image.open(result["file"]) as cleaned:
                before, after = np.array(image), np.array(cleaned)
            outside = np.ones((60, 96), dtype=bool)
            outside[18:32, 38:52] = False
            np.testing.assert_array_equal(before[outside], after[outside])
            np.testing.assert_array_equal(before[:, :, 3], after[:, :, 3])
            self.assertLess(int(np.max(np.abs(after[24, 44, :3].astype(int) - [20, 90, 180]))), 8)
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertEqual(result["status"], "pending_visual_review")
            self.assertEqual((result["width"], result["height"]), (96, 60))
            self.assertTrue((root / "clean/watermark-before-after.jpg").is_file())
            with self.assertRaises(ValueError):
                watermark.repair(source, root / "clean", boxes=[(38, 18, 52, 32)])

    def test_mask_is_required_and_must_match_actual_image_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, mask = root / "source.png", root / "mask.png"
            Image.new("RGB", (30, 20), "navy").save(source)
            with self.assertRaises(ValueError):
                watermark.repair(source, root / "out")
            Image.new("L", (20, 30), 255).save(mask)
            with self.assertRaises(ValueError):
                watermark.repair(source, root / "out", mask=mask)
            for value in (0, 255):
                Image.new("L", (30, 20), value).save(mask)
                with self.assertRaises(ValueError):
                    watermark.repair(source, root / "out", mask=mask)
            for box in ((-1, 0, 5, 5), (10, 10, 31, 20), (0, 0, 0, 5)):
                with self.assertRaises(ValueError):
                    watermark.repair(source, root / "out", boxes=[box])
            self.assertFalse((root / "out").exists())

    def test_binary_mask_can_select_disconnected_watermark_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, mask = root / "source.png", root / "mask.png"
            image = Image.new("RGB", (60, 40), "teal")
            region = Image.new("L", image.size)
            for box in ((10, 10, 14, 14), (40, 22, 44, 26)):
                ImageDraw.Draw(image).rectangle(box, fill="white")
                ImageDraw.Draw(region).rectangle(box, fill=255)
            image.save(source)
            region.save(mask)
            result = watermark.repair(source, root / "out", mask=mask)
            with Image.open(result["file"]) as repaired:
                self.assertLess(repaired.getpixel((12, 12))[0], 10)
                self.assertLess(repaired.getpixel((42, 24))[0], 10)

    def repeating_source(self, root, boxes):
        tile = Image.new("RGBA", (64, 56), (25, 45, 75, 255))
        draw = ImageDraw.Draw(tile)
        draw.polygon([(4, 17), (27, 5), (34, 24), (14, 31)], fill=(240, 95, 55, 255))
        draw.line((4, 26, 23, 14, 34, 26), fill="white", width=2)
        draw.rectangle((29, 30, 59, 45), fill=(120, 225, 210, 200))
        tile_array = np.array(tile)
        clean = np.tile(tile_array, (3, 3, 1))
        marked = clean.copy()
        for x0, y0, x1, y1 in boxes:
            area = marked[y0:y1, x0:x1, :3].astype(np.uint16)
            marked[y0:y1, x0:x1, :3] = (3 * area + 255) // 4
        source = root / "marked.png"
        Image.fromarray(marked).save(source)
        return source, clean, marked

    def test_repeat_recovers_source_pixels_across_edges_and_uses_only_unmasked_donors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boxes = [(0, 12, 25, 22), (64, 12, 89, 22), (135, 60, 158, 80), (25, 140, 60, 153)]
            source, clean, _ = self.repeating_source(root, boxes)
            result = watermark.repair(source, root / "out", boxes=boxes, method="repeat",
                                      periods=[(64, 0), (0, 56)])
            with Image.open(result["file"]) as recovered:
                np.testing.assert_array_equal(np.array(recovered), clean)
            self.assertEqual(result["unresolved_pixels"], 0)
            self.assertEqual(result["repeat_restored_pixels"], sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes))
            self.assertEqual(result["status"], "pending_visual_review")

    def test_misaligned_period_leaves_unresolved_pixels_without_a_clean_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boxes = [(7, 12, 28, 20)]
            source, _, marked = self.repeating_source(root, boxes)
            result = watermark.repair(source, root / "out", boxes=boxes, method="repeat", periods=[(31, 0)])
            self.assertEqual(result["status"], "unresolved_regions")
            self.assertEqual(result["unresolved_pixels"], 21 * 8)
            self.assertFalse((root / "out/source-clean.png").exists())
            with Image.open(result["file"]) as preview:
                np.testing.assert_array_equal(np.array(preview), marked)
            with Image.open(result["unresolved_mask"]) as unresolved:
                self.assertEqual(unresolved.getbbox(), boxes[0])

    def test_flat_fallback_fills_uniform_gaps_and_leaves_detailed_gaps_for_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texture = [(x + 13, y + 12, x + 23, y + 22) for x in (0, 64, 128) for y in (0, 56, 112)]
            flat = [(x + 42, y + 8, x + 50, y + 16) for x in (0, 64, 128) for y in (0, 56, 112)]
            source, _, _ = self.repeating_source(root, texture + flat)
            result = watermark.repair(source, root / "out", boxes=texture + flat, method="repeat",
                                      periods=[(64, 0), (0, 56)], fill_flat=True)
            self.assertEqual(result["flat_repaired_pixels"], 9 * 8 * 8)
            self.assertEqual(result["unresolved_pixels"], 9 * 10 * 10)
            self.assertEqual(result["repeat_restored_pixels"], 0)
            self.assertEqual(result["status"], "unresolved_regions")


if __name__ == "__main__":
    unittest.main()
