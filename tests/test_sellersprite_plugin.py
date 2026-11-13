import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/pod-image-selection/scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('sellersprite_plugin_client', SCRIPTS / 'sellersprite_plugin_client.py')
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


def entry(url='https://e.sellersprite.com/v2/extension/competitor-lookup/JP?asins=B000000001&version=5.0.6&language=zh_CN&extension=fixture&source=chrome',
          timestamp='2026-11-13T04:20:00Z', token='SECRET_TOKEN'):
    return {'startedDateTime': timestamp, 'request': {'method': 'GET', 'url': url, 'headers': [
        {'name': 'Auth-Token', 'value': token}, {'name': 'Auth-FP', 'value': 'SECRET_FP'},
        {'name': 'Random-Token', 'value': 'SECRET_DEVICE'}, {'name': 'Cookie', 'value': 'SECRET_COOKIE'},
        {'name': 'User-Agent', 'value': 'fixture'}]}}


def response(rows=None, market='https://www.amazon.com'):
    return {'code': 'OK', 'token': 'SECRET_RESPONSE', 'data': {'marketplace': market, 'total': 7519,
        'items': rows if rows is not None else [{'asin': 'B000000001', 'parent': 'B000000099',
            'units': 10, 'amzUnit': 99999, 'image': 'https://m.media-amazon.com/images/I/example.jpg?token=SECRET_IMAGE',
            'title': 'pet house', 'token': 'SECRET_PRODUCT'}]}}


class MemoryResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class SellerSpritePluginTests(unittest.TestCase):
    def write_har(self, directory, entries):
        path = Path(directory) / 'capture.har'
        path.write_text(json.dumps({'log': {'entries': entries}}))
        return path

    def test_session_selection_uses_capture_time_and_drops_cookies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_har(directory, [entry(token='NEW_TOKEN'), entry(timestamp='2026-11-12T04:20:00Z')])
            session = plugin.load_session(path)
            self.assertEqual(session['headers']['auth-token'], 'NEW_TOKEN')
            self.assertNotIn('cookie', session['headers'])
            self.assertEqual(session['parameters']['version'], '5.0.6')
            self.assertNotIn('asins', session['parameters'])

    def test_session_does_not_accept_other_hosts_login_routes_or_credentials_in_url(self):
        urls = ['https://evil.example/v2/extension/competitor-lookup/JP',
                'https://e.sellersprite.com:444/v2/extension/competitor-lookup/JP',
                'https://name:password@e.sellersprite.com/v2/extension/competitor-lookup/JP',
                'https://e.sellersprite.com/v2/extension/signin',
                'https://e.sellersprite.com/v2/extension/competitor-lookup/quick-view/JP']
        with tempfile.TemporaryDirectory() as directory:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(plugin.ApiError):
                    plugin.load_session(self.write_har(directory, [entry(url)]))

    def test_redacted_headers_fail_before_a_request(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(plugin.ApiError, 'MISSING_SESSION_HEADERS'):
                plugin.load_session(self.write_har(directory, [entry(token='[REDACTED]')]))

    def test_request_is_us_keyword_only_and_redirects_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            session = plugin.load_session(self.write_har(directory, [entry()]))
        opener = mock.Mock()
        opener.open.return_value = MemoryResponse(json.dumps(response()).encode())
        with mock.patch.object(plugin.urllib.request, 'build_opener', return_value=opener):
            document, _ = plugin.request_plugin(' pet house ', session)
        request = opener.open.call_args.args[0]
        u = urlsplit(request.full_url)
        query = parse_qs(u.query)
        self.assertEqual(u.netloc, 'e.sellersprite.com')
        self.assertEqual(u.path, '/v2/extension/competitor-lookup/US')
        self.assertEqual(query['qt'], ['K'])
        self.assertEqual(query['q'], ['pet house'])
        self.assertNotIn('asins', query)
        self.assertNotIn('Cookie', request.headers)
        self.assertEqual(document['code'], 'OK')
        self.assertIsNone(plugin.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example/'))

    def test_200_authorization_failure_and_wrong_market_are_errors(self):
        session = {'parameters': {'version': '5.0.6'}, 'extension_version': '5.0.6', 'headers': {}}
        for document, expected in [({'code': 'ERR_NEED_RE_AUTHORIZED', 'message': 'SECRET_ERROR'}, 'ERR_NEED_RE_AUTHORIZED'),
                                   (response(market='https://www.amazon.co.jp'), 'UNEXPECTED_MARKETPLACE'),
                                   (response(rows=[]), 'EMPTY_PAGE_WITH_MATCHES')]:
            opener = mock.Mock()
            opener.open.return_value = MemoryResponse(json.dumps(document).encode())
            with self.subTest(expected=expected), \
                 mock.patch.object(plugin.urllib.request, 'build_opener', return_value=opener), \
                 self.assertRaisesRegex(plugin.ApiError, expected):
                plugin.request_plugin('pet house', session)

    def test_normalization_retains_variants_missing_sales_and_ranking_scope(self):
        row = response()['data']['items'][0]
        rows = [dict(row, asin='B000000001', units=None), dict(row, asin='B000000002', units=0),
                dict(row, asin='B000000003', units=20), dict(row, asin='B000000004', units=20),
                dict(row, asin='B000000005', units=-1)]
        result, public = plugin.normalize_plugin('pet house', response(rows=rows))
        self.assertEqual([p['asin'] for p in result['products']], ['B000000003', 'B000000004', 'B000000002'])
        self.assertEqual(len(result['incomplete']), 2)
        self.assertEqual(result['ranking_scope'], 'returned_candidates_only')
        self.assertFalse(result['global_top_30_verified'])
        self.assertEqual(result['returned_candidates'], 5)
        self.assertEqual(result['total_matches'], 7519)
        self.assertNotIn('SECRET', json.dumps(public))

    def test_ranking_returns_thirty_unique_asins_by_parent_sales(self):
        source = response()['data']['items'][0]
        rows = [dict(source, asin='B' + str(i).zfill(9), units=i, amzUnit=99999-i) for i in range(1, 41)]
        rows.append(dict(rows[-1], units=9999))
        result, _ = plugin.normalize_plugin('pet house', response(rows=rows))
        self.assertEqual(len(result['products']), 30)
        self.assertEqual([p['parent_units_30d'] for p in result['products']], list(range(40, 10, -1)))
        self.assertEqual([p['rank'] for p in result['products']], list(range(1, 31)))
        self.assertEqual(len({p['parent_asin'] for p in result['products']}), 1)

    def test_invalid_numbers_and_unsafe_images_are_marked_incomplete(self):
        source = response()['data']['items'][0]
        rows = [dict(source, asin='B000000001', units=True),
                dict(source, asin='B000000002', units=float('nan')),
                dict(source, asin='B000000003', units=float('inf')),
                dict(source, asin='B000000004', image='javascript:bad')]
        result, public = plugin.normalize_plugin('pet house', response(rows=rows))
        self.assertEqual(result['products'], [])
        self.assertEqual(len(result['incomplete']), 4)
        self.assertFalse(result['returned_rows_complete'])
        json.dumps(public, allow_nan=False)

    def test_invalid_query_stops_before_network(self):
        with mock.patch.object(plugin.urllib.request, 'build_opener') as opener:
            for query in [None, '', '  ', 'x' * 501]:
                with self.subTest(query=query), self.assertRaises(ValueError):
                    plugin.request_plugin(query, {})
            opener.assert_not_called()

    def test_cli_keeps_credentials_and_response_login_fields_out_of_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_har(directory, [entry()])
            output = Path(directory) / 'out'
            stdout = io.StringIO()
            with mock.patch.object(plugin, 'request_plugin', return_value=(response(), 800)), contextlib.redirect_stdout(stdout):
                code = plugin.main(['search', '--query', 'pet house', '--session-har', str(path), '--out', str(output)])
            self.assertEqual(code, 0)
            for file in output.glob('*.json'):
                self.assertNotIn('SECRET', file.read_text())
            self.assertNotIn('SECRET', stdout.getvalue())
            result = json.loads((output / 'ranked-products.json').read_text())
            self.assertEqual(result['products'][0]['parent_units_30d'], 10)

    def test_missing_har_stops_before_network_and_output_cannot_replace_har(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(plugin.os.environ, {}, clear=True), \
             mock.patch.object(plugin, 'request_plugin') as request, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(plugin.main(['search', '--query', 'pet house', '--out', directory]), 2)
            request.assert_not_called()
            path = Path(directory) / 'request-plan.json'
            original = path.read_text()
            self.assertEqual(plugin.main(['plan', '--query', 'pet house', '--out', directory, '--session-har', str(path)]), 2)
            self.assertEqual(path.read_text(), original)

    def test_browser_input_normalizes_without_a_har_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            input_file = Path(directory) / 'browser-products.json'
            input_file.write_text(json.dumps({'keyword': 'pet house', 'marketplace': 'US',
                'items': response()['data']['items'], 'explicit_empty': False}))
            out = Path(directory) / 'out'
            with mock.patch.object(plugin, 'load_session') as load, \
                 mock.patch.object(plugin, 'request_plugin') as request, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(plugin.main(['normalize', '--query', 'pet house', '--input', str(input_file), '--out', str(out)]), 0)
                load.assert_not_called(); request.assert_not_called()
            result = json.loads((out / 'ranked-products.json').read_text())
            self.assertEqual(result['source'], 'sellersprite_extension_browser')
            self.assertEqual(result['products'][0]['parent_units_30d'], 10)
            self.assertIsNone(result['total_matches'])
            self.assertNotIn('observed_server_units_desc', result)
            public = json.loads((out / 'sellersprite-candidates.json').read_text())
            self.assertNotIn('code', public)
            self.assertFalse(public['business_response_observed'])

    def test_browser_scope_mismatch_is_not_normalized_as_us_results(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            source = Path(directory) / 'input.json'
            source.write_text(json.dumps({'keyword': 'pet house', 'marketplace': 'JP', 'items': []}))
            self.assertEqual(plugin.main(['normalize', '--query', 'pet house', '--input', str(source), '--out', str(Path(directory) / 'out')]), 2)

    def test_browser_empty_state_requires_explicit_ui_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.json'
            source.write_text(json.dumps({'keyword': 'pet house', 'marketplace': 'US', 'items': []}))
            out = Path(directory) / 'out'
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(plugin.main(['normalize', '--query', 'pet house', '--input', str(source), '--out', str(out)]), 2)
            source.write_text(json.dumps({'keyword': 'pet house', 'marketplace': 'US', 'items': [], 'explicit_empty': True}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(plugin.main(['normalize', '--query', 'pet house', '--input', str(source), '--out', str(out)]), 0)
            self.assertEqual(json.loads((out / 'api-status.json').read_text())['status'], 'empty')


if __name__ == '__main__':
    unittest.main()
