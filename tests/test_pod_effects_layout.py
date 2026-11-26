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
sys.path.insert(0, str(SCRIPT.parent))
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

    def test_final_portrait_images_meet_minimum_and_preserve_every_view(self):
        source, data = self.source()
        data['design_fingerprint'] = 'a' * 64
        source.write_text(json.dumps(data))
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.parent.iterdir()}
        result = layout.finalize(source, '4:5', self.root / 'final')
        self.assertEqual((result['stage'], result['status'], result['exported_count']), ('final', 'complete', 2))
        self.assertEqual(result['output_aspect_ratio'], '4:5')
        self.assertEqual(result['design_fingerprint'], data['design_fingerprint'])
        for effect in result['effects']:
            with Image.open(self.root / 'final' / effect['file']) as output:
                self.assertEqual(output.size, (2000, 2500))
                self.assertEqual(output.getpixel((0, 0)), (255, 255, 255))
                with Image.open(effect['source_file']) as original:
                    self.assertEqual(output.crop(effect['content_box']).size, (2000, 2000))
                    self.assertEqual(output.crop(effect['content_box']).getpixel((0, 0)), original.getpixel((0, 0)))
                self.assertTrue(effect['upscaled'])
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.parent.iterdir()})
        self.assertEqual([e['view_id'] for e in result['effects']], [e['view_id'] for e in data['effects']])
        html = (self.root / 'final/effects.html').read_text()
        self.assertEqual(html.count('<figure>'), 2)
        self.assertIn('宽:高 4:5', html)

    def test_capture_sha256_rejects_an_image_replaced_before_ratio_processing(self):
        source, data = self.source()
        effect = data['effects'][0]
        image = source.parent / effect['file']
        effect['sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
        source.write_text(json.dumps(data))
        Image.new('RGB', (100, 100), 'black').save(image)
        with self.assertRaisesRegex(ValueError, '重新导出视角 101'):
            layout.finalize(source, '4:5', self.root / 'final')

    def test_wide_ratio_gives_every_view_the_same_exact_dimensions(self):
        source, _ = self.source(sizes=((100, 70), (80, 100)))
        result = layout.finalize(source, '16:9', self.root / 'wide')
        self.assertEqual((result['width'], result['height']), (3568, 2007))
        for effect in result['effects']:
            with Image.open(self.root / 'wide' / effect['file']) as output:
                self.assertEqual(output.width * 9, output.height * 16)
                content = output.crop(effect['content_box'])
                self.assertLessEqual(abs(content.width / content.height - effect['source_width'] / effect['source_height']), 0.001)

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

    def test_default_upscales_only_undersized_image_and_copies_larger_image_bytes(self):
        source, data = self.source(sizes=((658, 658), (2400, 2100)))
        output = self.root / 'final'
        with patch.object(sys, 'argv', [str(SCRIPT), '--manifest', str(source), '--out', str(output)]), \
                contextlib.redirect_stdout(io.StringIO()):
            layout.main()
        result = json.loads((output / 'effects-manifest.json').read_text())
        self.assertEqual([(e['width'], e['height']) for e in result['effects']], [(2000, 2000), (2400, 2100)])
        self.assertIsNone(result['output_aspect_ratio'])
        large = result['effects'][1]
        self.assertEqual((output / large['file']).read_bytes(), (source.parent / data['effects'][1]['file']).read_bytes())
        self.assertFalse(large['upscaled'])

    def test_larger_effect_with_explicit_ratio_is_padded_without_resampling(self):
        source, _ = self.source(sizes=((2400, 2100),))
        result = layout.finalize(source, '4:5', self.root / 'portrait')
        effect = result['effects'][0]
        self.assertEqual((effect['width'], effect['height']), (2400, 3000))
        self.assertEqual(effect['content_box'], [0, 450, 2400, 2550])
        self.assertFalse(effect['upscaled'])

    def test_enhanced_view_preserves_canvas_layout_and_other_view_bytes(self):
        source, data = self.source(sizes=((500, 500), (2400, 3000)))
        candidate = self.root / 'candidate.png'
        Image.new('RGB', (2100, 2100), 'teal').save(candidate)
        output = self.root / 'enhanced'
        with self.assertRaisesRegex(ValueError, '102'):
            layout.finalize(source, '4:5', output, {'102': candidate})
        result = layout.finalize(source, '4:5', output, {'101': candidate})
        selected, original = result['effects']
        self.assertEqual(selected['resize_method'], 'ai_super_resolution')
        self.assertEqual(selected['content_box'], [0, 250, 2000, 2250])
        self.assertEqual(selected['detail_source']['sha256'], hashlib.sha256(candidate.read_bytes()).hexdigest())
        with Image.open(output / selected['file']) as image:
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(image.getpixel((1000, 1000)), (0, 128, 128))
        self.assertEqual(original['resize_method'], 'original')
        self.assertEqual((output / original['file']).read_bytes(), (source.parent / data['effects'][1]['file']).read_bytes())


if __name__ == '__main__':
    unittest.main()
