import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/pod-image-selection/scripts/huaban_tools.py'
SPEC = importlib.util.spec_from_file_location('huaban_tools', SCRIPT)
hb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hb)


class HuabanMaterialsTests(unittest.TestCase):
    def test_200_challenge_and_business_errors_are_not_success(self):
        bad = [('text/html', b'<script>document.cookie="challenge"</script>'),
               ('application/json', b'{"code":401,"pins":[],"message":"login"}'),
               ('application/json', b'{"data":"not candidates"}'),
               ('application/json', b'{"error":"expired","pins":[]}')]
        for content_type, body in bad:
            with self.subTest(body=body), self.assertRaises(hb.AccessError):
                hb.parse_api_response(200, content_type, body)
        self.assertEqual(hb.parse_api_response(200, 'application/json', b'{"pins":[]}'), {'pins': []})

    def test_dedup_unifies_pins_files_and_provenance(self):
        a = {'pin_id': 11, 'file_id': 100, 'file': {'key': 'a', 'width': 1200, 'height': 1600},
             'source_paths': [{'via': 'keyword', 'query': '猫'}]}
        b = {'pin_id': 12, 'file_id': 101, 'file': {'key': 'b'},
             'source_paths': [{'via': 'board', 'parent_board_id': '50'}]}
        bridge = {'pin_id': 13, 'file_id': 100, 'file': {'key': 'b'},
                  'source_paths': [{'via': 'similar', 'parent_pin_id': '9'}]}
        data = hb.normalize([{'pins': [a, b, bridge]}])
        self.assertEqual((len(data['candidates']), data['duplicates']), (1, 2))
        pin = data['candidates'][0]
        self.assertEqual(pin['pin_ids'], ['11', '12', '13'])
        self.assertEqual(pin['width'], 1200)
        self.assertTrue(any(p.get('parent_board_id') == '50' for p in pin['source_paths']))
        self.assertTrue(any(p.get('parent_pin_id') == '9' for p in pin['source_paths']))

    def test_board_preview_attaches_board_and_skips_private_pin(self):
        doc = {'boards': [{'board_id': 50, 'title': '花', 'pins': [
            {'pin_id': 11}, {'pin_id': 12, 'is_private': 1}]}]}
        pin = hb.normalize([doc], via='related_board')['candidates'][0]
        self.assertEqual(pin['board_id'], 50)
        self.assertEqual(pin['board_title'], '花')
        self.assertEqual(pin['source_paths'][0]['parent_board_id'], '50')
        self.assertEqual(pin['rights_status'], '灵感参考，授权待核实')

    def test_dom_preview_does_not_claim_original_resolution_or_change_signed_url(self):
        url = 'https://gd-hbimg-edge.huaban.com/abc-X_fw240webp?auth_key=keep-exact'
        dom = {'pin_id': 11, 'image_url': url, 'dimension_source': 'dom_thumbnail',
               'thumbnail_width': 240, 'thumbnail_height': 360}
        api = {'pin_id': 11, 'file': {'key': 'abc-X', 'width': 1080, 'height': 1620}}
        first = hb.normalize([{'pins': [dom]}])['candidates'][0]
        self.assertIsNone(first['width'])
        self.assertEqual(first['image_url'], url)
        enriched = hb.normalize([{'pins': [dom, api]}])['candidates'][0]
        self.assertEqual(enriched['width'], 1080)
        self.assertEqual(enriched['dimension_source'], 'api_file_metadata')
        self.assertEqual(enriched['image_url'], url)

    def test_gallery_escapes_remote_text_and_rejects_script_links(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'gallery.html'
            doc = {'candidates': [{'pin_id': '11', 'title': '<script>alert(1)</script>',
                                  'pin_url': 'javascript:alert(1)', 'image_url': 'data:image/svg+xml,bad',
                                  'visual_notes': '<img src=x onerror=bad>', 'decision': '保留'}]}
            hb.render_gallery(doc, out)
            page = out.read_text()
            self.assertNotIn('<script>', page)
            self.assertNotIn('href="javascript:', page)
            self.assertNotIn('src="data:', page)
            self.assertIn('&lt;script&gt;', page)
            self.assertIn('尺寸未核实', page)

    def test_urls_encode_query_and_restrict_credential_destination(self):
        url = hb.api_url('search', query='猫 & 木刻', limit=3)
        self.assertEqual(parse_qs(urlsplit(url).query)['text'], ['猫 & 木刻'])
        hb.validate_api_url(url)
        for bad in ['https://evil.example/v3/search/file', 'http://huaban.com/v3/search/file',
                    'https://huaban.com/v3/me', 'https://huaban.com@evil.example/v3/pins/11']:
            with self.subTest(url=bad), self.assertRaises(ValueError):
                hb.validate_api_url(bad)

    def test_invalid_secret_file_does_not_print_its_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / 'headers.json'
            secret.write_text('{"Authorization":"Bearer DO_NOT_PRINT" invalid}')
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = hb.main(['fetch', '--url', 'https://huaban.com/v3/pins/11',
                                  '--headers-file', str(secret), '--output', str(Path(directory) / 'out.json')])
            self.assertEqual(result, 1)
            self.assertNotIn('DO_NOT_PRINT', stderr.getvalue())
            self.assertFalse((Path(directory) / 'out.json').exists())

    def test_selected_gallery_keeps_only_reviewed_keep_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'gallery.html'
            doc = {'candidates': [{'title': 'chosen', 'decision': '保留'},
                                  {'title': 'pending', 'decision': '待看图'},
                                  {'title': 'rejected', 'decision': '排除'}]}
            result = hb.render_gallery(doc, out, selected_only=True)
            self.assertEqual(result['cards'], 1)
            self.assertNotIn('pending', out.read_text())
            self.assertNotIn('rejected', out.read_text())

    def test_browser_saved_preview_is_copied_to_portable_gallery(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'browser.webp'
            source.write_bytes(b'RIFF-browser-downloaded-preview')
            out = Path(directory) / 'report' / 'gallery.html'
            result = hb.render_gallery({'candidates': [{'title': 'saved', 'local_image': str(source)},
                                                       {'title': 'rejected', 'decision': '排除'}]}, out)
            self.assertEqual((result['cards'], result['downloaded']), (1, 1))
            self.assertEqual((out.parent / 'gallery_assets/001.webp').read_bytes(), source.read_bytes())
            self.assertIn('src="gallery_assets/001.webp"', out.read_text())


if __name__ == '__main__':
    unittest.main()
