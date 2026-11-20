import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = ImageDraw = None

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/pod-image-selection/scripts/pod_effects_layout.py'
layout = None
if Image is not None:
    spec = importlib.util.spec_from_file_location('pod_effects_layout', SCRIPT)
    layout = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(layout)


@unittest.skipIf(layout is None, "Run with uv run --with 'pillow>=10,<12' for image tests")
class FinalEffectsLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def source(self, sizes=((100, 100), (100, 100))):
        directory = self.root / 'raw'
        directory.mkdir()
        effects = []
        for index, size in enumerate(sizes, 1):
            file = f'effect-{index:02d}-{100 + index}.png'
            image = Image.new('RGB', size, (30, 80, 150))
            ImageDraw.Draw(image).rectangle((0, 0, 10, 10), fill='red')
            image.save(directory / file)
            effects.append({'index': index, 'view_id': str(100 + index), 'file': file, 'name': f'视角 {index}'})
        data = {'stage': 'capture', 'status': 'complete', 'expected_count': len(effects),
                'exported_count': len(effects), 'expected_view_ids': [e['view_id'] for e in effects],
                'missing_view_ids': [], 'effects': effects}
        target = directory / 'effects-manifest.json'
        target.write_text(json.dumps(data))
        return target, data

    def test_width_height_ratio_parsing_and_normalization(self):
        for value, expected in [('4:5', (4, 5)), (' 8：10 ', (4, 5)), ('1920x1080', (16, 9)),
                                ('1.5:1', (3, 2)), ('1:1', (1, 1))]:
            with self.subTest(value=value):
                self.assertEqual(layout.parse_ratio(value), expected)
        for invalid in [None, '', '0:5', '-1:5', '4:0', '4:5:6', 'wide', 'nan:1']:
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                layout.parse_ratio(invalid)

    def test_final_portrait_images_preserve_every_original_pixel_and_view(self):
        source, data = self.source()
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.parent.iterdir()}
        result = layout.finalize(source, '4:5', self.root / 'final')
        self.assertEqual((result['stage'], result['status'], result['exported_count']), ('final', 'complete', 2))
        self.assertEqual(result['output_aspect_ratio'], '4:5')
        for effect in result['effects']:
            with Image.open(self.root / 'final' / effect['file']) as output:
                self.assertEqual(output.size, (100, 125))
                self.assertEqual(output.getpixel((0, 0)), (255, 255, 255))
                with Image.open(effect['source_file']) as original:
                    self.assertEqual(output.crop(effect['content_box']).tobytes(), original.tobytes())
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.parent.iterdir()})
        self.assertEqual([e['view_id'] for e in result['effects']], [e['view_id'] for e in data['effects']])
        html = (self.root / 'final/effects.html').read_text()
        self.assertEqual(html.count('<figure>'), 2)
        self.assertIn('宽:高 4:5', html)

    def test_wide_ratio_gives_every_view_the_same_exact_dimensions(self):
        source, _ = self.source(sizes=((100, 70), (80, 100)))
        result = layout.finalize(source, '16:9', self.root / 'wide')
        self.assertEqual((result['width'], result['height']), (192, 108))
        for effect in result['effects']:
            with Image.open(self.root / 'wide' / effect['file']) as output:
                self.assertEqual(output.width * 9, output.height * 16)
                self.assertEqual(output.crop(effect['content_box']).size,
                                 (effect['source_width'], effect['source_height']))

    def test_incomplete_capture_cannot_be_called_a_complete_final_delivery(self):
        source, data = self.source()
        data['status'] = 'incomplete'
        data['missing_view_ids'] = ['999']
        source.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, '未完整导出'):
            layout.finalize(source, '4:5', self.root / 'final')
        self.assertFalse((self.root / 'final').exists())

    def test_final_directory_cannot_replace_original_capture(self):
        source, _ = self.source()
        with self.assertRaisesRegex(ValueError, '不能覆盖'):
            layout.finalize(source, '4:5', source.parent)

    def test_failed_image_is_recorded_as_missing_while_other_views_finish(self):
        source, _ = self.source()
        save = Image.Image.save

        def failing_save(image, target, *args, **kwargs):
            if Path(target).name == 'effect-02-102.png':
                raise OSError('fixture failure')
            return save(image, target, *args, **kwargs)

        with patch.object(Image.Image, 'save', failing_save), self.assertRaisesRegex(ValueError, '部分最终图片'):
            layout.finalize(source, '4:5', self.root / 'partial')
        manifest = json.loads((self.root / 'partial/effects-manifest.json').read_text())
        self.assertEqual((manifest['status'], manifest['exported_count']), ('incomplete', 1))
        self.assertEqual(manifest['missing_view_ids'], ['102'])
        self.assertIn('处理未完成', (self.root / 'partial/effects.html').read_text())

    def test_cli_requires_ratio_instead_of_silently_defaulting_to_square(self):
        with patch.object(sys, 'argv', [str(SCRIPT), '--manifest', 'raw.json', '--out', 'final']), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            layout.main()
        self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
