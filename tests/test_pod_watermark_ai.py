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

if cv2 is not None:
    script = Path(__file__).resolve().parents[1] / "skills/pod-image-selection/scripts/pod_watermark_ai.py"
    spec = importlib.util.spec_from_file_location("pod_watermark_ai", script)
    repair = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(repair)


@unittest.skipIf(cv2 is None, "Run with Pillow and opencv-python-headless")
class WatermarkAICompositeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        unit = Image.new("RGBA", (48, 40), (22, 47, 51, 190))
        draw = ImageDraw.Draw(unit)
        draw.polygon([(3, 14), (24, 4), (43, 33), (10, 37)], fill=(45, 165, 230, 255))
        draw.line((3, 15, 25, 4, 39, 27), fill="white", width=2)
        draw.ellipse((18, 15, 23, 20), fill="orange")
        self.clean = np.tile(np.array(unit), (2, 2, 1))
        self.mask = np.zeros((80, 96), dtype=bool)
        # The same obscured pixels occur in every tile. A second obscured spot
        # occurs only once and can be recovered from another original repeat.
        for x in (0, 48):
            for y in (0, 40):
                self.mask[y:y + 4, x + 16:x + 21] = True
        self.mask[22:26, 6:9] = True
        marked = self.clean.copy()
        marked[self.mask, :3] = (255, 115, 235)
        self.source = self.root / "source.png"
        self.mask_file = self.root / "mask.png"
        Image.fromarray(marked).save(self.source)
        Image.fromarray(self.mask.astype(np.uint8) * 255).save(self.mask_file)

    def test_composite_repeats_only_the_patch_and_preserves_native_size_and_alpha(self):
        out = self.root / "plan"
        plan = repair.prepare(self.source, self.mask_file, out, tile=(5, 7, 48, 40), context=5)
        ys, xs = repair.phase_indices((50, 58), (5, 5, 48, 40))
        offset_unit = self.clean[7:47, 5:53]
        proposed = offset_unit[np.ix_(ys, xs)].copy()
        model_mask = repair.read_image(plan["ai_mask"], "L") > 127
        # Simulate a minor color change by the model in unmasked areas.
        proposed[~model_mask, 0] = np.minimum(proposed[~model_mask, 0].astype(np.int16) + 5, 255)
        candidate = self.root / "candidate.png"
        Image.fromarray(proposed).save(candidate)
        self.assertFalse(np.any(model_mask[20:24, 6:9]))
        result = repair.composite(out / "ai-repair-plan.json", candidate, self.root / "result")
        self.assertEqual(result["status"], "pending_visual_review")
        actual = repair.read_image(result["output"])
        np.testing.assert_array_equal(actual, self.clean)
        self.assertEqual(repair.read_image(self.root / "result/review-repeat-2x2.png").shape, (80, 96, 4))

    def test_bad_candidate_alignment_does_not_produce_a_composite(self):
        out = self.root / "plan"
        plan = repair.prepare(self.source, self.mask_file, out)
        candidate = self.root / "shifted.png"
        Image.fromarray(np.roll(repair.read_image(plan["input"]), 20, axis=1)).save(candidate)
        result = repair.composite(out / "ai-repair-plan.json", candidate, self.root / "result")
        self.assertEqual(result["status"], "alignment_rejected")
        self.assertFalse((self.root / "result/source-ai-review.png").exists())
