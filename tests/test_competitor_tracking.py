import contextlib
import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock
import urllib.error
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('competitor_client', ROOT / 'skills/amazon-jp-competitor-tracking/scripts/competitor_client.py')
client = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(Path(SPEC.origin).parent))
try:
    SPEC.loader.exec_module(client)
finally:
    sys.path.pop(0)
ASIN = 'B008H0Y15I'
OTHER = 'B07WQGD7G1'
SCOPE = client.window('2026-11-23')
SESSION = {'extension_version': '5.0.7', 'parameters': {'version': '5.0.7'},
           'headers': {'auth-token': 'fixture-private-token', 'auth-fp': 'fixture-fp', 'random-token': 'fixture-random'}}


class Opener:
    def __init__(self, payloads):
        self.payloads, self.requests = iter(payloads), []

    def open(self, request, timeout):
        self.requests.append(request)
        value = next(self.payloads)
        if isinstance(value, Exception):
            raise value
        return contextlib.closing(io.BytesIO(json.dumps(value).encode()))


class FakeClient:
    session = SESSION

    def __init__(self, values):
        self.values, self.calls = iter(values), []

    def get(self, route, params):
        self.calls.append((route, params))
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def product(asin=ASIN):
    return {'asin': asin, 'title': 'テスト用 商品', 'brand': '試験ブランド',
            'image': 'https://m.media-amazon.com/images/I/fixture.jpg', 'price': 3255,
            'reviews': 0, 'rating': 4.3, 'symbol': '￥', 'month_units': '100+', 'bsr': 5283,
            'bsr_label': 'ホーム＆キッチン', 'unavailable': False,
            'bsr_list': [{'rank': 54, 'label': '空気清浄機用フィルター', 'main': False},
                         {'rank': 5283, 'label': 'ホーム＆キッチン', 'main': True}],
            'auth-token': 'must-not-save', 'account': {'password': 'must-not-save'}}


class CompetitorTests(unittest.TestCase):
    def test_numeric_child_badge_is_displayed_as_threshold_without_altering_raw(self):
        data = product()
        data['month_units'] = 100
        row = client.normalize(ASIN, {'product': client.product_row([data], ASIN)},
                               client.normalize_changes([], SCOPE), [], SCOPE['end'])
        self.assertEqual(row['child_monthly_purchased'], '100+')
        self.assertEqual(row['child_monthly_purchased_raw'], 100)
        self.assertEqual(client.purchase_threshold('100+'), '100+')
        self.assertEqual(client.purchase_threshold(1000), '1,000+')

    def test_current_offer_only_merges_after_japan_delivery_is_verified(self):
        amazon = {'status': 'ok', 'delivery_destination': {'country_code': 'JP', 'verified': True},
                  'data': {'asin': ASIN, 'price': 3255, 'reference_price': 5170, 'discount_percent': 37,
                           'buy_box': True, 'stock': 'in_stock'}}
        sources = {'product': client.product_row([product()], ASIN), 'amazon': amazon}
        row = client.normalize(ASIN, sources, client.normalize_changes([], SCOPE), [], SCOPE['end'])
        self.assertEqual(row['reference_price'], 5170)
        self.assertEqual(row['discount_percent'], 37)
        self.assertTrue(row['buy_box'])
        self.assertEqual(row['stock'], 'in_stock')
        amazon['delivery_destination']['verified'] = False
        row = client.normalize(ASIN, sources, client.normalize_changes([], SCOPE), [], SCOPE['end'])
        for key in ('reference_price', 'discount_percent', 'buy_box', 'stock'):
            self.assertIsNone(row[key])

    def test_browser_offer_recognizes_explicit_low_stock_chinese_message(self):
        observation = {'url': 'https://www.amazon.co.jp/dp/' + ASIN, 'title': '商品',
                       'observed_at': '2026-11-24T03:32:41Z', 'country_label': '日本',
                       'postal_code': '163-8001', 'offer_postal_code': '163-8001',
                       'price_text': 'JP¥3,680', 'cart': {'enabled': True, 'label': '加入购物车'},
                       'availability_text': '库存仅剩 2 件 - 欲购从速。'}
        self.assertEqual(client.browser_offer(observation)['data']['stock'], 'in_stock')

    def test_complete_product_is_ok_and_missing_current_offer_reports_browser_followup(self):
        data = dict(product(), parent=OTHER, title='テスト用 商品 1枚入り')
        amazon = mock.Mock()
        amazon.fetch.return_value = {'status': 'ok', 'delivery_destination': {'country_code': 'JP', 'verified': True},
            'data': {'asin': ASIN, 'currency': 'JPY', 'price': 3255, 'reference_price': 5170,
                     'discount_percent': 37, 'buy_box': True, 'stock': 'in_stock'}}
        with tempfile.TemporaryDirectory() as tmp:
            vendor = FakeClient([[data], {'items': [{'asin': ASIN, 'units': 175}]}, []])
            result = client.collect([ASIN], vendor, SCOPE, Path(tmp) / 'complete', amazon=amazon)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['browser_fallback_asins'], [])
            vendor = FakeClient([[data], {'items': [{'asin': ASIN, 'units': 175}]}, []])
            result = client.collect([ASIN], vendor, SCOPE, Path(tmp) / 'missing-offer')
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['browser_fallback_asins'], [ASIN])

    def test_modes_cannot_overwrite_each_others_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            product_dir, history_dir = Path(tmp) / 'product', Path(tmp) / 'history'
            api = FakeClient([[product()], {'items': [{'asin': ASIN, 'units': 175}]}, []])
            client.collect([ASIN], api, SCOPE, product_dir)
            before = (product_dir / 'report.html').read_bytes()
            untouched = FakeClient([])
            with self.assertRaisesRegex(client.ApiError, 'OUTPUT_ALREADY_HAS_RESULTS'):
                client.collect_listing_changes([ASIN], untouched, SCOPE, product_dir)
            self.assertEqual(untouched.calls, [])
            self.assertEqual((product_dir / 'report.html').read_bytes(), before)
            api = FakeClient([[{'timePoint': '2026-11-20', 'changeType': 2}]])
            client.collect_listing_changes([ASIN], api, SCOPE, history_dir)
            with self.assertRaisesRegex(client.ApiError, 'OUTPUT_ALREADY_HAS_RESULTS'):
                client.collect([ASIN], untouched, SCOPE, history_dir)
            self.assertEqual(untouched.calls, [])

    def test_failed_json_replacement_preserves_previous_results_and_cleans_temporary_file(self):
        core = sys.modules['competitor_core']
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'snapshot.json'
            client.write_json(path, {'value': 'previous'})
            with mock.patch.object(core.os, 'replace', side_effect=OSError('fixture replacement failure')):
                with self.assertRaises(OSError):
                    client.write_json(path, {'value': 'new'})
            self.assertEqual(json.loads(path.read_text()), {'value': 'previous'})
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ['snapshot.json'])

    def test_captcha_is_not_retried_during_vendor_auth_recovery(self):
        amazon = mock.Mock()
        amazon.fetch.return_value = {'status': 'failed', 'category': 'AMAZON_HUMAN_VERIFICATION_REQUIRED',
                                     'delivery_destination': {'country_code': 'JP', 'verified': False}}
        first = FakeClient([client.ApiError('ERR_NEED_RE_AUTHORIZED')])
        second = FakeClient([[product()], {'items': [{'asin': ASIN, 'units': 175, 'month_units': 100}]}, []])
        with tempfile.TemporaryDirectory() as tmp:
            client.collect([ASIN], first, SCOPE, tmp, amazon=amazon)
            client.collect([ASIN], second, SCOPE, tmp, resume=True, amazon=amazon)
            self.assertEqual(amazon.fetch.call_count, 1)
            row = json.loads((Path(tmp) / 'products.json').read_text())['products'][0]
            self.assertEqual(row['child_monthly_purchased'], '100+')
            self.assertFalse(row['delivery_destination']['verified'])
            self.assertIsNone(row['buy_box'])

    def test_verified_offer_confirms_identity_without_vendor_snapshots(self):
        amazon = {'status': 'ok', 'delivery_destination': {'country_code': 'JP', 'verified': True},
                  'data': {'asin': ASIN, 'title': 'Confirmed offer', 'price': 3255, 'buy_box': True}}
        row = client.normalize(ASIN, {'amazon': amazon}, client.normalize_changes([], SCOPE), [], SCOPE['end'])
        self.assertEqual(row['title'], 'Confirmed offer')
        self.assertEqual(row['status'], 'partial')
        self.assertFalse(row['media_change_observed'])

    def test_browser_title_alone_can_supply_declared_pack_quantity_and_source(self):
        snapshot = {'status': 'ok', 'collection_method': 'browser_headless',
                    'delivery_destination': {'country_code': 'JP', 'verified': True},
                    'collected_at': '2026-11-24T08:00:00Z',
                    'data': {'asin': ASIN, 'title': '交換用フィルター 2個パック', 'price': 5387}}
        row = client.normalize(ASIN, {'amazon': snapshot}, client.normalize_changes([], SCOPE), [], SCOPE['end'])
        self.assertEqual(row['package_quantity'], 2)
        self.assertEqual(row['field_sources']['price'], 'amazon_jp_browser_offer.price')
        self.assertEqual(row['offer_observed_at'], snapshot['collected_at'])

    def test_title_and_product_specs_precede_image_and_variation_label(self):
        asin = 'B0F284QTLM'
        data = dict(product(asin), title='洗える脱臭フィルター 280 337 0168',
                    variation_list=[{'asin': asin, 'attr': '8枚入り'}])
        changes = client.normalize_changes([], SCOPE)
        def resolve(title, specs):
            amazon = {'status': 'ok', 'delivery_destination': {'country_code': 'JP', 'verified': True},
                      'data': {'asin': asin, 'title': title, 'quantity_specs': specs}}
            return client.normalize(asin, {'product': data, 'amazon': amazon}, changes, [], SCOPE['end'])

        specs = [{'label': '商品数量', 'value': '１'}, {'label': '内包装数量', 'value': '1'}]
        title = resolve('脱臭フィルター １枚入り', [{'label': '商品数量', 'value': '3'}])
        self.assertEqual(title['package_quantity'], 1)
        self.assertEqual(title['field_sources']['package_quantity'], 'explicit_listing_title')
        body = resolve(data['title'], specs)
        self.assertEqual(body['package_quantity'], 1)
        self.assertEqual(body['field_sources']['package_quantity'], 'amazon_jp_verified_delivery.quantity_specs')
        self.assertIsNone(client.package_quantity_candidate(body))
        pack = resolve(data['title'], [{'label': '入数', 'value': '２枚'}])
        self.assertEqual(pack['package_quantity'], 2)
        conflict = resolve(data['title'], specs + [{'label': '商品個数', 'value': '2'}])
        self.assertIsNone(conflict['package_quantity'])
        self.assertIsNone(client.package_quantity_candidate(conflict))
        missing = resolve(data['title'], [])
        self.assertIsNone(missing['package_quantity'])
        self.assertEqual(client.package_quantity_candidate(missing), {'asin': asin, 'main_image': data['image']})

    def test_main_image_count_is_bound_to_the_local_run_and_cannot_replace_text(self):
        asin = 'B0F284QTLM'
        data = dict(product(asin), title='洗える脱臭フィルター 280 337 0168')
        title_data = dict(product(OTHER), title='脱臭フィルター 1枚入り')
        vendor = FakeClient([[data], {'items': [{'asin': asin, 'units': 4}]}, [],
                             [title_data], {'items': [{'asin': OTHER, 'units': 4}]}, []])
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            summary = client.collect([asin, OTHER], vendor, SCOPE, output)
            self.assertEqual(summary['package_quantity_review'], [{'asin': asin, 'main_image': data['image']}])
            evidence = dict(summary['package_quantity_review'][0], quantity=1, evidence='一块带独立外框的滤网')
            with self.assertRaisesRegex(client.ApiError, 'PACKAGE_REVIEW_PRODUCT_OR_IMAGE_MISMATCH'):
                client.review_quantities(output, [dict(evidence, asin=OTHER)])
            with self.assertRaisesRegex(client.ApiError, 'PACKAGE_REVIEW_PRODUCT_OR_IMAGE_MISMATCH'):
                client.review_quantities(output, [dict(evidence, main_image=data['image'] + '.other')])
            self.assertEqual(client.review_quantities(output, [dict(evidence, quantity=None)])['quantities_added'], 0)
            self.assertEqual(client.review_quantities(output, [evidence])['quantities_added'], 1)
            rows = json.loads((output / 'products.json').read_text())['products']
            self.assertEqual([row['package_quantity'] for row in rows], [1, 1])
            self.assertEqual(rows[0]['field_sources']['package_quantity'], 'amazon_jp_main_image_review')
            with (output / 'products.csv').open(encoding='utf-8-sig') as stream:
                self.assertEqual([r['数量装来源'] for r in csv.DictReader(stream)], ['主图识别', '标题'])

    def test_browser_offer_supplements_failed_http_quote_in_new_report(self):
        observation = {'url': 'https://www.amazon.co.jp/dp/' + ASIN, 'title': 'テスト用 商品',
                       'observed_at': '2026-11-23T07:22:25Z', 'country_label': '日本',
                       'postal_code': '163-8001', 'offer_postal_code': '163-8001',
                       'price_text': 'JP¥3,255', 'reference_price_text': 'JP¥5,170',
                       'reference_text': '市场价: JP¥5,170', 'discount_text': '-37%',
                       'cart': {'label': '加入购物车', 'enabled': True}, 'availability_text': '现在有货。'}
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / 'http', Path(tmp) / 'merged'
            amazon = mock.Mock()
            amazon.fetch.return_value = {'status': 'failed', 'category': 'AMAZON_HUMAN_VERIFICATION_REQUIRED'}
            vendor = FakeClient([[product()], {'items': [{'asin': ASIN, 'units': 175}]}, []])
            client.collect([ASIN], vendor, SCOPE, source, amazon=amazon)
            with self.assertRaisesRegex(client.ApiError, 'AMAZON_DESTINATION_NOT_CONFIRMED'):
                client.merge_browser_offers(source, output, [dict(observation, offer_postal_code='100-0001')])
            with self.assertRaisesRegex(client.ApiError, 'BROWSER_OFFER_ASIN_MISMATCH_OR_DUPLICATE'):
                client.merge_browser_offers(source, output, [dict(observation, url='https://www.amazon.co.jp/dp/' + OTHER)])
            client.merge_browser_offers(source, output, [observation])
            row = json.loads((output / 'products.json').read_text())['products'][0]
            self.assertEqual([row[key] for key in client.OFFER_FIELDS], [3255, 5170, 37, True, 'in_stock'])
            self.assertEqual(row['field_sources']['discount_percent'], 'amazon_jp_browser_offer.discount_percent')
            self.assertEqual(row['parent_sales_30d'], 175)
            self.assertEqual(row['missing_fields'], ['package_quantity', 'parent_asin'])
            self.assertTrue((output / 'evidence' / f'{ASIN}-amazon.json').is_file())
            self.assertTrue((output / 'evidence' / f'{ASIN}-amazon-browser.json').is_file())
            self.assertIn('Amazon 浏览器商品页', (output / 'report.html').read_text())

    def test_jp_asins_urls_files_and_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input.csv'
            path.write_text('ASIN,group\nB008H0Y15I,A\nB008H0Y15I,B\nB07WQGD7G1,C\n', encoding='utf-8-sig')
            self.assertEqual(client.read_inputs(['https://www.amazon.co.jp/name/dp/B008H0Y15I/ref=abc'], path), [ASIN, OTHER])
            path = Path(tmp) / 'input.json'
            path.write_text(json.dumps([{'url': 'https://amazon.co.jp/gp/product/' + ASIN}, OTHER]))
            self.assertEqual(client.read_inputs(input_file=path), [ASIN, OTHER])
        for value in ['https://amazon.com/dp/' + ASIN, 'https://amazon.co.jp.evil.test/dp/' + ASIN,
                      'https://user:pass@amazon.co.jp/dp/' + ASIN, 'https://www.amazon.co.jp/dp/' + ASIN + 'X', 'bad']:
            with self.assertRaises(client.ApiError):
                client.parse_asin(value)

    def test_window_is_seven_japan_dates_and_requires_aware_time(self):
        self.assertEqual(SCOPE['start'], '2026-11-17T00:00:00+09:00')
        self.assertEqual(SCOPE['end'], '2026-11-23T23:59:59.999000+09:00')
        self.assertEqual(client.window('2026-11-23T23:30:00-08:00')['end'][:10], '2026-11-24')
        with self.assertRaises(client.ApiError):
            client.window('2026-11-23T12:00:00')

    def test_http_checks_business_code_and_uses_only_fixed_jp_endpoints(self):
        opener = Opener([{'code': 'OK', 'data': [product()]},
                         {'code': 'ERR_NEED_RENEWAL_AUTHORIZED', 'data': {}},
                         {'code': 'ERR_NEED_RENEWAL_AUTHORIZED', 'data': {}}])
        from test_competitor_session import FakeGate
        api = client.Client(SESSION, opener=opener, sleep=lambda _: None, gate=FakeGate())
        row = api.get('/v2/extension/asin/JP', {'asins': ASIN})
        self.assertEqual(row[0]['asin'], ASIN)
        parsed = urlsplit(opener.requests[0].full_url)
        self.assertEqual(parsed.hostname, 'e.sellersprite.com')
        self.assertEqual(parse_qs(parsed.query)['version'], ['5.0.7'])
        self.assertEqual(opener.requests[0].get_header('Auth-token'), 'fixture-private-token')
        with self.assertRaisesRegex(client.ApiError, 'ERR_NEED_RENEWAL_AUTHORIZED'):
            api.get('/v2/extension/asin/JP', {'asins': ASIN})
        with self.assertRaisesRegex(client.ApiError, 'ENDPOINT_NOT_ALLOWED'):
            api.get('/v2/extension/asin/US', {'asins': ASIN})

    def test_robot_verification_stops_remaining_sources_and_preserves_known_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            vendor = FakeClient([[product()], client.ApiError('ERR_ROBOT_CHECK')])
            result = client.collect([ASIN, OTHER], vendor, SCOPE, tmp)
            self.assertEqual(result['status'], 'verification_required')
            self.assertEqual(result['category'], 'ERR_ROBOT_CHECK')
            self.assertEqual(len(vendor.calls), 2)
            rows = json.loads((Path(tmp) / 'products.json').read_text())['products']
            self.assertEqual(rows[0]['title'], product()['title'])
            self.assertIsNone(rows[0]['media_change_observed'])

    def test_histories_are_enum_not_bitmask_and_filter_dates(self):
        events = [{'timePoint': '2026-11-20 10:00:00', 'changeType': k} for k in range(8)]
        events += [{'timePoint': '2026-11-16 10:00:00', 'changeType': 2}]
        result = client.normalize_changes(events, SCOPE)
        self.assertEqual([e['changeType'] for e in result['events']], [2, 4, 6, 7])
        self.assertEqual(len(result['events']), 4)




    def test_changes_only_all_period_exports_media_marker_without_chart_or_amazon(self):
        scope = client.history_window('2026-11-23', True)
        event = {'timePoint': '2025-08-31 00:00:00', 'changeType': 4,
                 'private_notes': 'must-not-save', 'auth-token': 'must-not-save'}
        api = FakeClient([[event]])
        with tempfile.TemporaryDirectory() as tmp:
            result = client.collect_listing_changes([ASIN], api, scope, tmp)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['media_changed_products'], 1)
            self.assertEqual(api.calls, [('/v2/extension/listing/JP/asin-change/' + ASIN,
                                         {'startTime': 0, 'endTime': scope['end_ms']})])
            payload = json.loads((Path(tmp) / 'changes.json').read_text())
            self.assertEqual(payload['products'][0]['media_change_observed'], True)
            self.assertIn('全部周期图片/视频变化', (Path(tmp) / 'changes.csv').read_text(encoding='utf-8-sig'))
            for path in Path(tmp).rglob('*'):
                if path.is_file():
                    self.assertNotIn('must-not-save', path.read_text(encoding='utf-8-sig'))

    def test_changes_only_auth_resume_keeps_marker_evidence_and_rechecks_identity(self):
        first = FakeClient([[], client.ApiError('ERR_NEED_RE_AUTHORIZED')])
        second = FakeClient([[product()]])
        with tempfile.TemporaryDirectory() as tmp:
            result = client.collect_listing_changes([ASIN], first, SCOPE, tmp)
            self.assertEqual(result['status'], 'authorization_required')
            self.assertIsNone(json.loads((Path(tmp) / 'changes.json').read_text())['products'][0]['media_change_observed'])
            result = client.collect_listing_changes([ASIN], second, SCOPE, tmp, resume=True)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual([route for route, params in second.calls], ['/v2/extension/asin/JP'])
            row = json.loads((Path(tmp) / 'changes.json').read_text())['products'][0]
            self.assertFalse(row['media_change_observed'])

    def test_changes_only_empty_list_does_not_confirm_an_unknown_asin(self):
        api = FakeClient([[], []])
        with tempfile.TemporaryDirectory() as tmp:
            result = client.collect_listing_changes([ASIN], api, SCOPE, tmp)
            self.assertEqual(result['status'], 'failed')
            row = json.loads((Path(tmp) / 'changes.json').read_text())['products'][0]
            self.assertIsNone(row['media_change_observed'])

    def test_normal_collection_exports_media_marker_and_refreshes_legacy_evidence(self):
        api = FakeClient([[product()], {'items': [{'asin': ASIN, 'units': 175}]}, []])
        with tempfile.TemporaryDirectory() as tmp:
            client.collect([ASIN], api, SCOPE, tmp)
            evidence_path = Path(tmp) / 'evidence' / f'{ASIN}-changes.json'
            saved = json.loads(evidence_path.read_text())
            saved['data'].pop('schema')
            evidence_path.write_text(json.dumps(saved))
            event = {'timePoint': '2026-11-20 12:34:56', 'changeType': 2,
                     'previousImages': ['old'], 'currentImages': ['new']}
            fresh = FakeClient([[event]])
            client.collect([ASIN], fresh, SCOPE, tmp, resume=True)
            self.assertEqual(len(fresh.calls), 1)
            row = json.loads((Path(tmp) / 'products.json').read_text())['products'][0]
            self.assertTrue(row['media_change_observed'])
            with (Path(tmp) / 'products.csv').open(encoding='utf-8-sig') as stream:
                exported = list(csv.DictReader(stream))[0]
            self.assertEqual(exported['近7天图片/视频变化'], '是')

    def test_unknown_history_never_becomes_no_changes(self):
        with self.assertRaises(client.ApiError):
            client.normalize_changes(None, SCOPE)
        for events in [[{'timePoint': 'bad', 'changeType': 2}], [{'timePoint': '2026-11-22', 'changeType': 9}]]:
            self.assertIsNone(client.normalize_changes(events, SCOPE)['media_change_observed'])
        self.assertFalse(client.normalize_changes([], SCOPE)['media_change_observed'])
        events = [{'timePoint': 'bad', 'changeType': 2}, {'timePoint': '2026-11-22', 'changeType': 2}]
        self.assertTrue(client.normalize_changes(events, SCOPE)['media_change_observed'])

    def test_sales_badge_zero_and_unverified_cart_remain_distinct(self):
        p = client.product_row([product()], ASIN)
        sales = client.product_row({'items': [{'asin': ASIN, 'units': 157, 'parent': OTHER}]}, ASIN, sales=True)
        row = client.normalize(ASIN, {'product': p, 'sales': sales}, client.normalize_changes([], SCOPE), [], SCOPE['end'])
        self.assertEqual(row['parent_sales_30d'], 157)
        self.assertEqual(row['child_monthly_purchased'], '100+')
        self.assertEqual(row['review_count'], 0)
        self.assertEqual(row['currency'], 'JPY')
        self.assertEqual(row['sub_ranks'][0]['rank'], 54)
        for field in ('buy_box', 'stock', 'reference_price', 'discount_percent'):
            self.assertIsNone(row[field])
        self.assertNotIn('must-not-save', json.dumps(p))
        with self.assertRaisesRegex(client.ApiError, 'ASIN_NOT_RETURNED'):
            client.product_row([product(OTHER)], ASIN)

    def test_rate_limit_stops_requests_but_keeps_all_asin_rows_and_good_sources(self):
        api = FakeClient([[product()], client.ApiError('HTTP_429')])
        with tempfile.TemporaryDirectory() as tmp:
            summary = client.collect([ASIN, OTHER], api, SCOPE, tmp)
            report = json.loads((Path(tmp) / 'products.json').read_text())
            self.assertEqual(len(api.calls), 2)
            self.assertEqual(len(report['products']), 2)
            self.assertEqual(report['products'][0]['title'], 'テスト用 商品')
            self.assertIsNone(report['products'][0]['media_change_observed'])
            self.assertEqual(report['products'][1]['status'], 'failed')
            self.assertEqual(summary['category'], 'HTTP_429')

    def test_auth_recovery_reuses_only_successful_evidence(self):
        first = FakeClient([[product()], client.ApiError('ERR_NEED_RENEWAL_AUTHORIZED')])
        second = FakeClient([{'items': [{'asin': ASIN, 'units': 0}]}, []])
        with tempfile.TemporaryDirectory() as tmp:
            result = client.collect([ASIN], first, SCOPE, tmp)
            self.assertEqual(result['status'], 'authorization_required')
            client.collect([ASIN], second, SCOPE, tmp, resume=True)
            self.assertEqual([call[0] for call in second.calls], ['/v2/extension/competitor-lookup/JP',
                '/v2/extension/listing/JP/asin-change/' + ASIN])
            row = json.loads((Path(tmp) / 'products.json').read_text())['products'][0]
            self.assertEqual(row['parent_sales_30d'], 0)
            self.assertEqual(row['title'], 'テスト用 商品')
            self.assertFalse(row['media_change_observed'])
            with self.assertRaisesRegex(client.ApiError, 'RESUME_INPUT_OR_WINDOW_MISMATCH'):
                client.collect([OTHER], second, SCOPE, tmp, resume=True)

    def test_report_escaping_csv_formulas_and_private_response_fields(self):
        p = product()
        p['title'] = '=HYPERLINK("unsafe")<script>alert(1)</script>'
        api = FakeClient([[p], {'items': [{'asin': ASIN, 'units': 157, 'auth-token': 'must-not-save'}]}, []])
        with tempfile.TemporaryDirectory() as tmp:
            client.collect([ASIN], api, SCOPE, tmp)
            root = Path(tmp)
            page = (root / 'report.html').read_text()
            self.assertNotIn('<script>', page)
            self.assertIn('&lt;script&gt;', page)
            for f in root.rglob('*'):
                if f.is_file():
                    self.assertNotIn('must-not-save', f.read_text(encoding='utf-8-sig'))
                    self.assertNotIn('fixture-private-token', f.read_text(encoding='utf-8-sig'))
            with (root / 'products.csv').open(encoding='utf-8-sig') as stream:
                row = list(csv.DictReader(stream))[0]
            self.assertTrue(row['标题'].startswith("'="))

    def test_session_reads_allowlisted_headers_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'session.json'
            p.write_text(json.dumps(dict(SESSION, format='sellersprite-http-session-v1', extra='not-used')))
            value = client.load_session(p)
            self.assertNotIn('extra', value)
            data = json.loads(p.read_text())
            data['headers']['auth-token'] = 'bad\nheader'
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(client.ApiError, 'INVALID_HTTP_SESSION'):
                client.load_session(p)

    def test_unclassified_rank_list_is_unknown_rather_than_empty(self):
        data = product()
        data['bsr_list'] = [{'rank': 54, 'label': 'Unclassified rank'}]
        row = client.normalize(ASIN, {'product': client.product_row([data], ASIN)},
                               client.normalize_changes([], SCOPE), [], SCOPE['end'])
        self.assertIsNone(row['sub_ranks'])

    def test_empty_history_does_not_confirm_an_unknown_product(self):
        api = FakeClient([[], {'crawlAsins': [ASIN]}, []])
        with tempfile.TemporaryDirectory() as tmp:
            result = client.collect([ASIN], api, SCOPE, tmp)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['usable_products'], 0)
            row = json.loads((Path(tmp) / 'products.json').read_text())['products'][0]
            self.assertEqual(row['errors'][1]['code'], 'PRODUCT_NOT_CACHED')
            self.assertEqual(row['history']['status'], 'unconfirmed_product')
            self.assertIsNone(row['media_change_observed'])


if __name__ == '__main__':
    unittest.main()
