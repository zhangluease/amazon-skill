import base64
import gzip
import hashlib
import random
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import quote

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = ImageDraw = None

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/pod-image-selection/scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = load_script("podqhy_client")
art = load_script("pod_artwork") if Image is not None else None


def flat(identifier, width, height, edit=1, size=2000):
    return {"id": identifier, "name": "face-%s" % identifier,
            "config": {"edit": edit, "flatWidth": width, "flatHeight": height, "size": size}}


class PodqhyApiTests(unittest.TestCase):
    def test_plain_encoded_and_gzip_responses(self):
        doc = {"data": [{"name": "左片", "id": 45026}]}
        raw = json.dumps(doc, ensure_ascii=False).encode()
        encoded = base64.b64encode(quote(raw.decode(), safe="").encode()).replace(b"D", b"DoNnIeR")
        for body in (raw, encoded, gzip.compress(encoded)):
            self.assertEqual(api.decode_response(body), doc)

    def test_login_and_business_failure_are_not_success(self):
        for body in (b"<html>login</html>", b'{"data":[],"status_code":401}',
                     b'{"data":{},"error":"expired"}', b'{"data":"invalid"}', b"\xff"):
            with self.subTest(body=body), self.assertRaises(api.AccessError):
                api.decode_response(body)

    def test_material_request_uses_real_rectangular_template_and_scale(self):
        params = api.material_params("ID123", {"data": [flat(1, 3000, 1000, size=1500),
                                                        flat(2, 500, 2200),
                                                        flat(3, 9000, 9000, edit=0)]})
        self.assertEqual((params["width"], params["height"], params["maxFlatId"]), (1500, 500, 1))
        self.assertEqual((params["maxFlatWidth"], params["maxFlatHeight"], params["scale"]),
                         (3000, 2200, 0.5))


@unittest.skipIf(art is None, "Run with uv run --with 'pillow>=10,<12' for artwork tests")
class PodArtworkTests(unittest.TestCase):
    def test_reviewed_detail_candidate_uses_planned_size_and_keeps_reference_alpha(self):
        import pod_image_size

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, candidate, wrong = (root / name for name in ("source.png", "candidate.png", "wrong.png"))
            Image.new("RGBA", (500, 500), (20, 20, 20, 128)).save(source)
            Image.new("RGB", (2200, 2200), "teal").save(candidate)
            Image.new("RGB", (2200, 2000), "teal").save(wrong)
            with self.assertRaisesRegex(ValueError, "宽高比"):
                pod_image_size.size_file(source, root / "bad.png", wrong)
            info = pod_image_size.size_file(source, root / "enhanced.png", candidate)
            self.assertEqual(info["resize_method"], "ai_super_resolution")
            with Image.open(info["file"]) as image:
                self.assertEqual(image.size, (2000, 2000))
                self.assertEqual(image.getpixel((1000, 1000)), (0, 128, 128, 128))

    def test_white_background_removed_but_enclosed_white_preserved(self):
        image = Image.new("RGBA", (100, 100), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 80, 80), fill="teal")
        draw.rectangle((35, 35, 65, 65), fill="white")
        result = art.remove_edge_white(image)
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((25, 25)), image.getpixel((25, 25)))
        self.assertEqual(result.getpixel((50, 50)), (255, 255, 255, 255))

    def test_transparency_and_full_color_are_distinguished(self):
        colored = Image.new("RGBA", (60, 60), "navy")
        opaque = art.analyze(colored)
        self.assertEqual(opaque["classification"], "full_print")
        self.assertFalse(opaque["has_transparency"])
        colored.putalpha(128)
        self.assertTrue(art.analyze(colored)["has_transparency"])

    def test_all_editable_surfaces_receive_individual_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jpg"
            image = Image.new("RGB", (100, 100), "white")
            ImageDraw.Draw(image).ellipse((25, 25, 75, 75), fill="teal")
            image.save(source)
            template = {"data": [flat(10, 120, 180), flat(11, 80, 40), flat(12, 20, 20, edit=0)]}
            manifest = art.prepare(source, template, Path(directory) / "out")
            self.assertEqual([s["id"] for s in manifest["surfaces"]], [10, 11])
            for surface, expected in zip(manifest["surfaces"], [(120, 180), (80, 40)]):
                with Image.open(surface["file"]) as output:
                    self.assertEqual(output.size, expected)
                    self.assertEqual(output.mode, "RGBA")
                    self.assertEqual(output.getpixel((0, 0))[3], 0)
            self.assertTrue(manifest["surfaces"][0]["upscaled"])
            self.assertEqual(manifest["status"], "prepared_pending_surface_review")

    def test_existing_alpha_is_preserved_without_automatic_white_cutout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "alpha.png"
            image = Image.new("RGBA", (40, 40), (255, 255, 255, 128))
            image.save(source)
            result = art.prepare(source, {"data": [flat(1, 40, 40)]}, Path(directory) / "out")
            surface = result["surfaces"][0]
            self.assertEqual(surface["background_action"], "preserve")
            with Image.open(surface["file"]) as output:
                self.assertEqual(output.getpixel((0, 0)), (255, 255, 255, 128))

    def test_classification_happens_after_cover_crop(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            image = Image.new("RGB", (300, 100), "white")
            ImageDraw.Draw(image).rectangle((100, 0, 199, 99), fill="navy")
            image.save(source)
            result = art.prepare(source, {"data": [flat(1, 100, 100)]}, Path(directory) / "out")
            self.assertEqual(result["source_analysis"]["classification"], "non_full_print")
            surface = result["surfaces"][0]
            self.assertEqual(surface["standardized_analysis"]["classification"], "full_print")
            self.assertEqual(surface["background_action"], "preserve")

    def test_contain_keeps_aspect_ratio_and_transparent_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "wide.png"
            Image.new("RGB", (80, 40), "red").save(source)
            result = art.prepare(source, {"data": [flat(1, 80, 80)]},
                                 Path(directory) / "out", fit="contain", background="preserve")
            with Image.open(result["surfaces"][0]["file"]) as output:
                self.assertEqual(output.size, (80, 80))
                self.assertEqual(output.getchannel("A").getbbox(), (0, 20, 80, 60))

    def test_one_shared_upload_keeps_source_aspect_for_differently_shaped_faces(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            image = Image.new("RGBA", (120, 40), "navy")
            ImageDraw.Draw(image).rectangle((0, 0, 20, 39), fill="orange")
            image.save(source)
            manifest = art.prepare(source, {"data": [flat(1, 40, 40), flat(2, 120, 40),
                                                       flat(3, 120, 40), flat(4, 9, 9, edit=0)]},
                                   Path(directory) / "out", background="preserve")
            plan = manifest["upload_plan"]
            self.assertEqual(plan["strategy"], "upload_once_copy_all")
            self.assertEqual(plan["surface_ids"], [1, 2, 3])
            self.assertEqual(plan["source_surface_id"], 1)
            with Image.open(plan["file"]) as upload:
                self.assertEqual(upload.size, (6000, 2000))
                self.assertEqual(upload.convert("RGBA").getpixel((0, 0)), (255, 165, 0, 255))
            with Image.open(manifest["surfaces"][0]["file"]) as square:
                self.assertEqual(square.getpixel((0, 0)), (0, 0, 128, 255))
            self.assertEqual(hashlib.sha256(Path(plan["file"]).read_bytes()).hexdigest(), plan["sha256"])
            self.assertEqual(len(list((Path(directory) / "out").glob("upload-*.png"))), 1)

    def test_shared_background_cleanup_does_not_remove_white_opened_by_a_face_crop(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            image = Image.new("RGB", (120, 60), "white")
            ImageDraw.Draw(image).rectangle((20, 10, 99, 49), fill="teal")
            ImageDraw.Draw(image).rectangle((30, 20, 89, 39), fill="white")
            image.save(source)
            manifest = art.prepare(source, {"data": [flat(1, 40, 60)]}, Path(directory) / "out")
            with Image.open(manifest["surfaces"][0]["file"]) as output:
                self.assertEqual(output.getpixel((0, 30))[3], 255)  # Enclosed white stays white.
                self.assertEqual(output.getpixel((0, 0))[3], 0)  # Original edge white is removed.

    def test_upload_limit_does_not_shrink_pixels_or_discard_alpha(self):
        size_limit = 47_000
        noise = random.Random(42).randbytes(160 * 120 * 3)
        opaque = Image.frombytes("RGB", (160, 120), noise).convert("RGBA")
        result, content, extension = art.encode_upload(opaque, size_limit)
        self.assertEqual(result.size, opaque.size)
        self.assertEqual(extension, "jpg")
        self.assertLessEqual(len(content), size_limit)
        opaque.putalpha(128)
        with self.assertRaisesRegex(ValueError, "保留像素尺寸与透明度"):
            art.encode_upload(opaque, size_limit)


if __name__ == "__main__":
    unittest.main()
