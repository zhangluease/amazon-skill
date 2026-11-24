import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/amazon-jp-competitor-tracking/scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    from competitor_core import ApiError, Client, load_session, is_login_mask, scalar, request_parameters
    from competitor_client import collect, window, collect_listing_changes
finally:
    sys.path.pop(0)

ASIN = 'B07RPLXMXD'
PRODUCT = '/v2/extension/asin/JP'
SALES = '/v2/extension/competitor-lookup/JP'
SESSION = {'extension_version': '5.0.7', 'parameters': {'version': '5.0.7'},
           'headers': {'auth-token': 'old-fixture-token', 'auth-fp': 'fixture-fp',
                       'random-token': 'fixture-random'}}
COMPLETE = {'asin': ASIN, 'title': 'Filter', 'brand': 'Fixture',
            'image': 'https://m.media-amazon.com/images/I/fixture.jpg',
            'bsr': 100, 'reviews': 0, 'rating': 4}


def ok(data):
    return {'code': 'OK', 'data': data}


class Opener:
    def __init__(self, responses):
        self.responses, self.requests = iter(responses), []

    def open(self, request, timeout):
        self.requests.append(request)
        return contextlib.closing(io.BytesIO(json.dumps(next(self.responses)).encode()))


class FakeGate:
    def __init__(self):
        self.pauses = []
        self.verified_count = 0

    def hold(self, **kwargs):
        return contextlib.nullcontext()

    def pace(self, interval):
        pass

    def pause(self, reason, kind):
        self.pauses.append((reason, kind))

    def verified(self):
        self.verified_count += 1


def api(responses, **kwargs):
    opener = Opener(responses)
    return Client(copy.deepcopy(SESSION), opener=opener, sleep=lambda _: None, gate=FakeGate(), **kwargs), opener


class SessionTests(unittest.TestCase):
    def test_valid_data_does_not_login_and_empty_history_is_a_valid_result(self):
        client, opener = api([ok([COMPLETE]), ok([])])
        self.assertEqual(client.get(PRODUCT, {'asins': ASIN})[0]['asin'], ASIN)
        self.assertEqual(client.get('/v2/extension/listing/JP/asin-change/' + ASIN, {}), [])
        self.assertFalse(client.renewal_attempted)
        self.assertEqual(len(opener.requests), 2)

    def test_masked_sales_data_triggers_token_login_and_returns_real_values(self):
        masked = {'items': [dict(COMPLETE, bsr=779, units='****', price='****', month_units='****')]}
        unlocked = {'items': [dict(COMPLETE, bsr=779, units=175, price=3255, month_units=100)]}
        client, opener = api([ok(masked), ok({'token': 'renewed-fixture-token'}), ok(unlocked)])
        self.assertEqual(client.get(SALES, {'asins': ASIN}), unlocked)
        self.assertEqual(client.auth_events[0]['reason'], 'PLUGIN_LOGIN_REQUIRED')
        self.assertEqual(len(opener.requests), 3)

    def test_masked_product_price_requires_login_even_with_other_fields_present(self):
        masked = dict(COMPLETE, bsr=779, price='****')
        client, _ = api([ok([masked]), ok({'token': 'renewed-fixture-token'}), ok([COMPLETE])])
        self.assertEqual(client.get(PRODUCT, {'asins': ASIN}), [COMPLETE])
        self.assertEqual(client.auth_events[0]['reason'], 'PLUGIN_LOGIN_REQUIRED')

    def test_remaining_login_mask_requires_browser_recovery_and_is_never_no_changes(self):
        masked = dict(COMPLETE, bsr=779, price='****')
        client, opener = api([ok([masked]), ok({'token': 'renewed-fixture-token'}), ok([masked])])
        with tempfile.TemporaryDirectory() as directory:
            scope = window('2026-11-24')
            result = collect([ASIN], client, scope, directory)
            self.assertEqual(result['status'], 'authorization_required')
            self.assertEqual(result['category'], 'PLUGIN_LOGIN_REQUIRED')
            self.assertEqual(len(opener.requests), 3)
            report = json.loads((Path(directory) / 'products.json').read_text())
            self.assertIsNone(report['products'][0]['price'])
            self.assertIsNone(report['products'][0]['media_change_observed'])
            self.assertEqual(report['products'][0]['root_rank'], 779)
            self.assertNotIn('****', json.dumps(report))
            recovered, requests = api([ok([COMPLETE]),
                ok({'items': [{'asin': ASIN, 'units': 175, 'month_units': 100}]}), ok([])])
            collect([ASIN], recovered, scope, directory, resume=True)
            self.assertEqual(urlsplit(requests.requests[0].full_url).path, PRODUCT)

    def test_masked_snapshot_survives_rejected_token_login_without_losing_known_rank(self):
        masked = dict(COMPLETE, bsr=779, price='****')
        client, _ = api([ok([masked]), {'code': 'ERR_NEED_AUTHORIZED'}])
        with tempfile.TemporaryDirectory() as directory:
            result = collect([ASIN], client, window('2026-11-24'), directory)
            self.assertEqual(result['status'], 'authorization_required')
            evidence = json.loads((Path(directory) / 'evidence' / f'{ASIN}-product.json').read_text())
            self.assertEqual(evidence['data']['bsr'], 779)
            self.assertIsNone(evidence['data']['price'])

    def test_masked_history_response_is_login_required_and_not_an_empty_history(self):
        client, _ = api([ok('****'), ok({'token': 'renewed-fixture-token'}), ok('****')])
        with tempfile.TemporaryDirectory() as directory:
            result = collect_listing_changes([ASIN], client, window('2026-11-24'), directory)
            self.assertEqual(result['status'], 'authorization_required')
            row = json.loads((Path(directory) / 'changes.json').read_text())['products'][0]
            self.assertIsNone(row['media_change_observed'])

    def test_masks_are_whole_values_and_do_not_match_ordinary_product_names(self):
        for value in ('****', '  ***  ', '＊＊＊＊'):
            self.assertTrue(is_login_mask(value))
            self.assertIsNone(scalar(value))
        for value in ('Filter * 2', 'Brand***', '100+', None, 0):
            self.assertFalse(is_login_mask(value))

    def test_auth_failure_renews_via_api_then_retries_and_saves_no_account_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'http-session.json'
            client, opener = api([{'code': 'ERR_NEED_RENEWAL_AUTHORIZED'},
                ok({'token': 'new-fixture-token', 'email': 'must-not-save', 'account': 'must-not-save'}),
                ok([COMPLETE])], session_file=path)
            client.get(PRODUCT, {'asins': ASIN})
            self.assertEqual([urlsplit(r.full_url).hostname for r in opener.requests],
                             ['e.sellersprite.com', 'www.sellersprite.com', 'e.sellersprite.com'])
            self.assertEqual(urlsplit(opener.requests[1].full_url).path, '/v2/extension/tk/signin')
            self.assertEqual(opener.requests[2].get_header('Auth-token'), 'new-fixture-token')
            self.assertEqual(load_session(path)['headers']['auth-token'], 'new-fixture-token')
            self.assertNotIn('must-not-save', path.read_text())
            self.assertNotIn('fixture-token', json.dumps(client.auth_events))
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_sparse_data_checks_login_once_and_still_missing_values_remain_unknown(self):
        sparse = {'items': [{'asin': ASIN, 'units': -1, 'month_units': 100}]}
        client, opener = api([ok(sparse), ok({'token': 'renewed-fixture-token'}), ok(sparse), ok(sparse)])
        first = client.get(SALES, {'asins': ASIN})
        client.get(SALES, {'asins': ASIN})
        self.assertEqual(first['items'][0]['units'], -1)
        self.assertEqual(len(client.auth_events), 1)
        self.assertEqual(client.auth_events[0]['reason'], 'missing_snapshot_data')
        self.assertEqual(client.auth_events[0]['status'], 'ok')
        self.assertEqual(len(opener.requests), 4)

    def test_sparse_unchanged_session_checks_once_without_token_signin(self):
        sparse = {'items': [{'asin': ASIN, 'units': -1, 'month_units': 100}]}
        client, opener = api([ok(sparse), ok({'token': SESSION['headers']['auth-token']}), ok(sparse)])
        client.get(SALES, {'asins': ASIN})
        client.get(SALES, {'asins': ASIN})
        self.assertFalse(client.renewal_attempted)
        self.assertEqual([urlsplit(r.full_url).path for r in opener.requests],
                         [SALES, '/v2/extension/client/latest', SALES])
        self.assertEqual(client.auth_events[0]['action'], 'session_check')

    def test_all_vendor_routes_get_common_signature_without_exposing_values_in_trace(self):
        from urllib.parse import parse_qs
        for route, params in [(PRODUCT, {'asins': ASIN}), (SALES, {'asins': ASIN, 'q': None}),
                              ('/v2/extension/client/latest', {}),
                              ('/v2/extension/listing/JP/asin-change/' + ASIN, {})]:
            client, opener = api([ok([])])
            client.missing_session_checked = True
            client.get(route, params)
            query = parse_qs(urlsplit(opener.requests[0].full_url).query)
            self.assertTrue(query['tk'][0])
            self.assertEqual(query['version'], ['5.0.7'])
            self.assertNotIn('q', query)
            trace = json.dumps(client.request_trace)
            self.assertNotIn('fixture-token', trace)
            self.assertNotIn(query['tk'][0], trace)
        self.assertEqual(request_parameters(SESSION, SALES, {'asins': ASIN, 'tk': 'preserved'})['tk'], 'preserved')

    def test_empty_product_data_is_requeried_after_checked_token_rotation(self):
        client, _ = api([ok([]), ok({'token': 'renewed-fixture-token'}), ok([COMPLETE])])
        self.assertEqual(client.get(PRODUCT, {'asins': ASIN}), [COMPLETE])

    def test_rejected_api_login_is_returned_for_the_existing_bounded_qr_flow(self):
        client, opener = api([{'code': 'ERR_NEED_RE_AUTHORIZED'}, {'code': 'ERR_NEED_AUTHORIZED'}])
        with self.assertRaisesRegex(ApiError, 'ERR_NEED_AUTHORIZED'):
            client.get(PRODUCT, {'asins': ASIN})
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(client.auth_events[0]['status'], 'failed')

    def test_invalid_token_response_preserves_previous_private_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'http-session.json'
            path.write_text(json.dumps(dict(SESSION, format='sellersprite-http-session-v1')))
            previous = path.read_text()
            client, _ = api([ok([]), ok({'token': 'bad\nheader'})], session_file=path)
            with self.assertRaisesRegex(ApiError, 'INVALID_SESSION_CHECK_RESPONSE'):
                client.get(PRODUCT, {'asins': ASIN})
            self.assertEqual(path.read_text(), previous)

    def test_permission_rate_limit_and_robot_errors_do_not_repeat_login(self):
        for code in ('ERR_GLOBAL_403', 'HTTP_403', 'HTTP_429', 'ERR_ROBOT_CHECK'):
            client, opener = api([{'code': code}])
            with self.subTest(code=code), self.assertRaisesRegex(ApiError, code):
                client.get(PRODUCT, {'asins': ASIN})
            self.assertEqual(len(opener.requests), 1)
            self.assertFalse(client.renewal_attempted)

    def test_auth_retry_is_bounded_even_if_the_renewed_token_is_rejected(self):
        client, opener = api([{'code': 'HTTP_401'}, ok({'token': 'renewed-fixture-token'}),
                             {'code': 'ERR_NEED_RE_AUTHORIZED'}])
        with self.assertRaisesRegex(ApiError, 'ERR_NEED_RE_AUTHORIZED'):
            client.get(PRODUCT, {'asins': ASIN})
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(len(client.auth_events), 1)

    def test_failed_recovery_keeps_safe_snapshot_and_resume_requeries_that_source(self):
        sparse = dict(COMPLETE, bsr=None, account='must-not-save')
        client, opener = api([ok([sparse]), ok({'token': 'renewed-fixture-token'}),
                             {'code': 'ERR_ROBOT_CHECK'}])
        with tempfile.TemporaryDirectory() as directory:
            scope = window('2026-11-24')
            result = collect([ASIN], client, scope, directory)
            self.assertEqual(result['status'], 'verification_required')
            report = json.loads((Path(directory) / 'products.json').read_text())
            self.assertEqual(report['products'][0]['title'], COMPLETE['title'])
            evidence = Path(directory) / 'evidence' / f'{ASIN}-product.json'
            self.assertTrue(json.loads(evidence.read_text())['recovery_pending'])
            self.assertNotIn('must-not-save', evidence.read_text())
            recovered, requests = api([ok([COMPLETE]),
                ok({'items': [{'asin': ASIN, 'units': 100, 'month_units': 50}]}), ok([])])
            collect([ASIN], recovered, scope, directory, resume=True)
            self.assertEqual(urlsplit(requests.requests[0].full_url).path, PRODUCT)
            self.assertNotIn('recovery_pending', json.loads(evidence.read_text()))


if __name__ == '__main__':
    unittest.main()
