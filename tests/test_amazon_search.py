import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock
import urllib.error
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/pod-image-selection/scripts/amazon_search.py'
SPEC = importlib.util.spec_from_file_location('amazon_search', SCRIPT)
search = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(search)


def page(asins=('B000000001',), more=False):
    cards = ''.join('<div data-component-type="s-search-result" data-asin="' + a + '"></div>' for a in asins)
    return ('<title>Amazon.com : pet house</title><input id="twotabsearchtextbox" value="pet house">'
            '<div class="s-main-slot">' + cards + '</div>'
            + ('<a class="s-pagination-next" href="/s?k=pet+house&page=2">Next</a>' if more else ''))


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AmazonSearchTests(unittest.TestCase):
    def test_extracts_unique_main_cards_and_ignores_carousels_and_script_text(self):
        text = page(('B000000001', 'B000000001', 'bad', 'B000000002'))
        text += '<div data-asin="B000000099"></div><script>"No results for pet house"</script>'
        parsed = search.parse_page(text, 'pet house')
        self.assertEqual(parsed['asins'], ['B000000001', 'B000000002'])
        self.assertFalse(parsed['explicit_empty'])

    def test_ordinary_get_uses_us_search_and_no_login_cookie(self):
        opener = mock.Mock()
        opener.open.return_value = Response(page().encode())
        result = search.search('pet house', opener=opener)
        request = opener.open.call_args.args[0]
        url = urlsplit(request.full_url)
        self.assertEqual(url.netloc, 'www.amazon.com')
        self.assertEqual(url.path, '/s')
        self.assertEqual(parse_qs(url.query), {'k': ['pet house'], 'page': ['1']})
        self.assertNotIn('cookie', {k.lower() for k in request.headers})
        self.assertEqual(result['asins_found'], 1)
        self.assertFalse(result['browser_started'])
        self.assertFalse(result['authenticated'])
        self.assertIsNone(search.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org'))

    def test_challenge_stops_after_one_request_and_is_not_empty_results(self):
        opener = mock.Mock()
        opener.open.return_value = Response(b'<title>Robot Check</title><form action="/errors/validateCaptcha"></form>')
        with self.assertRaisesRegex(search.SearchError, '^AMAZON_HUMAN_VERIFICATION_REQUIRED$'):
            search.search('pet house', pages=4, opener=opener)
        self.assertEqual(opener.open.call_count, 1)

    def test_http_error_and_unexpected_html_fail_without_retry(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError('https://www.amazon.com/s', 503, '', {}, io.BytesIO())
        with self.assertRaisesRegex(search.SearchError, '^AMAZON_HTTP_503$'):
            search.search('pet house', opener=opener)
        self.assertEqual(opener.open.call_count, 1)
        with self.assertRaisesRegex(search.SearchError, '^AMAZON_RESULTS_NOT_CONFIRMED$'):
            search.parse_page('<title>Amazon.com : pet house</title><p>Unavailable</p>', 'pet house')
        with self.assertRaisesRegex(search.SearchError, '^AMAZON_QUERY_NOT_CONFIRMED$'):
            search.parse_page(page(), 'different query')

    def test_empty_requires_explicit_search_page_evidence(self):
        parsed = search.parse_page(page(()) + '<p>No results for pet house</p>', 'pet house')
        self.assertTrue(parsed['explicit_empty'])
        self.assertEqual(parsed['asins'], [])

    def test_pagination_is_bounded_and_preserves_first_search_order(self):
        opener = mock.Mock()
        opener.open.side_effect = [Response(page(('B000000002', 'B000000001'), True).encode()),
                                   Response(page(('B000000001', 'B000000003')).encode())]
        result = search.search('pet house', pages=4, opener=opener)
        self.assertEqual(result['asins'], ['B000000002', 'B000000001', 'B000000003'])
        self.assertEqual(len(result['pages']), 2)
        self.assertEqual(opener.open.call_count, 2)
        with self.assertRaisesRegex(search.SearchError, '^INVALID_PAGE_COUNT$'):
            search.search('pet house', pages=5, opener=opener)


if __name__ == '__main__':
    unittest.main()
