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


if __name__ == "__main__":
    unittest.main()
