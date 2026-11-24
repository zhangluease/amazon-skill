import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/amazon-jp-competitor-tracking/scripts'))
from amazon_collection import AmazonQuoteClient

ASIN = 'B0D9W5646D'
OTHER = 'B008H0V15I'


class CollectionTests(unittest.TestCase):
    def test_invalid_http_cookie_file_leaves_browser_fallback_available(self):
        snapshot = {'status': 'ok', 'collection_method': 'browser_headless',
                    'data': {'asin': ASIN, 'price': 5387}, 'delivery_destination': {'verified': True}}
        runner = mock.Mock(return_value=mock.Mock(stdout=json.dumps(snapshot)))
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / 'amazon-http-cookies.txt'
            cookie_file.write_text('corrupt cookie jar')
            result = AmazonQuoteClient(tmp, runner=runner).fetch(ASIN)
            self.assertEqual(cookie_file.read_text(), 'corrupt cookie jar')
        self.assertEqual(result['data']['price'], 5387)
        self.assertEqual(result['http_attempt']['category'], 'AMAZON_SESSION_INVALID')

    def test_http_rate_limit_pauses_without_browser_retry(self):
        http, runner = mock.Mock(), mock.Mock()
        http.fetch.return_value = {'status': 'failed', 'category': 'AMAZON_HTTP_429'}
        with tempfile.TemporaryDirectory() as tmp:
            client = AmazonQuoteClient(tmp, http=http, runner=runner)
            self.assertEqual(client.fetch(ASIN)['category'], 'AMAZON_HTTP_429')
            self.assertTrue(client.fetch(OTHER)['not_attempted'])
        self.assertEqual(http.fetch.call_count, 1)
        runner.assert_not_called()

    def test_complete_http_price_does_not_launch_browser_for_absent_discount(self):
        http, runner = mock.Mock(), mock.Mock()
        http.fetch.return_value = {'status': 'ok', 'data': {'asin': ASIN, 'price': 5387, 'discount_percent': None}}
        with tempfile.TemporaryDirectory() as tmp:
            result = AmazonQuoteClient(tmp, http=http, runner=runner).fetch(ASIN)
        runner.assert_not_called()
        self.assertEqual(result['data']['price'], 5387)

    def test_failed_http_falls_back_and_preserves_source(self):
        http = mock.Mock()
        http.fetch.return_value = {'status': 'failed', 'category': 'AMAZON_CONTINUE_SHOPPING_REQUIRED'}
        snapshot = {'status': 'ok', 'collection_method': 'browser_headless',
                    'data': {'asin': ASIN, 'price': 5387}, 'delivery_destination': {'verified': True}}
        runner = mock.Mock(return_value=mock.Mock(stdout=json.dumps(snapshot)))
        with tempfile.TemporaryDirectory() as tmp:
            client = AmazonQuoteClient(tmp, http=http, runner=runner)
            result = client.fetch(ASIN)
        self.assertEqual(result['data']['price'], 5387)
        self.assertEqual(result['http_attempt']['category'], 'AMAZON_CONTINUE_SHOPPING_REQUIRED')
        self.assertEqual(client.browser_offers, 1)

    def test_wrong_child_offer_never_overwrites_original(self):
        http = mock.Mock()
        http.fetch.return_value = {'status': 'failed', 'category': 'AMAZON_CONTINUE_SHOPPING_REQUIRED'}
        runner = mock.Mock(return_value=mock.Mock(stdout=json.dumps({'status': 'ok', 'data': {'asin': OTHER},
                                                                     'delivery_destination': {'verified': True}})))
        with tempfile.TemporaryDirectory() as tmp:
            result = AmazonQuoteClient(tmp, http=http, runner=runner).fetch(ASIN)
        self.assertNotIn('data', result)
        self.assertEqual(result['browser_fallback']['category'], 'AMAZON_PRODUCT_NOT_CONFIRMED')

    def test_shared_verification_pause_prevents_http_and_browser_for_next_asin(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'amazon-browser-status.json').write_text(json.dumps({'status': 'verification_required',
                'category': 'AMAZON_CONTINUE_SHOPPING_REQUIRED', 'endpoint': 'https://www.amazon.co.jp/dp/' + OTHER}))
            http, runner = mock.Mock(), mock.Mock()
            result = AmazonQuoteClient(tmp, http=http, runner=runner).fetch(ASIN)
            http.fetch.assert_not_called()
            runner.assert_not_called()
        self.assertTrue(result['not_attempted'])
        self.assertTrue(result['endpoint'].endswith(ASIN))


if __name__ == '__main__':
    unittest.main()
