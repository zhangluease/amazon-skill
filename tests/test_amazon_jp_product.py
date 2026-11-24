import html
import importlib.util
import io
import json
import http.cookiejar
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('jp_product', ROOT / 'skills/amazon-jp-competitor-tracking/scripts/amazon_product.py')
jp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jp)
ASIN = 'B008H0V15I'


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def product(label='日本', kind='COUNTRY', asin=ASIN, stock='在庫あり。', buy_button=True):
    modal = {'url': '/portal-migration/hz/glow/get-rendered-address-selections?storeContext=NoStoreName',
             'ajaxHeaders': {'anti-csrftoken-a2z': 'private-fixture-modal-token'}}
    return ('<title>Amazon.co.jp</title><span id="glow-ingress-line2">' + label + '</span>'
            '<input id="glowDestinationType" value="' + kind + '">'
            '<span id="nav-global-location-data-modal-action" data-a-modal="' + html.escape(json.dumps(modal), quote=True) + '"></span>'
            '<input id="ASIN" value="' + asin + '"><span id="productTitle">テスト商品</span>'
            '<div id="corePriceDisplay_desktop_feature_div">'
            '<span class="savingsPercentage">-37%</span><span class="a-price priceToPay"><span class="a-offscreen">￥3,255</span></span>'
            '<span class="basisPrice">参考価格：<span class="a-price a-text-price"><span class="a-offscreen">￥5,170</span></span></span></div>'
            '<div id="availability">' + stock + '</div><div id="buybox">'
            + ('<input id="add-to-cart-button" type="submit">' if buy_button else '<span>現在おすすめの出品はありません</span>')
            + '</div>')


def flow(final=None, address=None):
    opener = mock.Mock()
    opener.open.side_effect = [Response(product(label='中国').encode()),
        Response(b'<script>var config = {CSRF_TOKEN: "private-fixture-update-token"};</script>'),
        Response(json.dumps({'sembuUpdated': 1, 'address': address or {'countryCode': 'JP', 'locationType': 'COUNTRY'}}).encode()),
        Response((final or product()).encode())]
    return opener


class JapanProductTests(unittest.TestCase):
    def test_primary_image_and_quantity_specs_are_extracted_from_the_product_page(self):
        markup = product() + '''
          <img id="landingImage" data-old-hires="https://m.media-amazon.com/images/I/hero.jpg">
          <table id="productOverview_feature_div">
            <tr><td>商品数量</td><td>１</td></tr><tr><td>内包装数量</td><td>１</td></tr>
            <tr><td>商品尺寸</td><td>19.3 x 15 x 19.3</td></tr>
            <tr><td>ユニット数</td><td>480 グラム</td></tr>
          </table>
          <table id="productDetails_techSpec_section_1"><tr><th>商品の個数</th><td>1.0 個</td></tr></table>
          <table id="anotherProduct"><tr><th>商品数量</th><td>5</td></tr></table>
          <select name="quantity"><option>2</option></select>'''
        offer = jp.parse_offer(jp.Page(markup), ASIN)
        self.assertEqual(offer['main_image'], 'https://m.media-amazon.com/images/I/hero.jpg')
        self.assertEqual([(s['label'], s['quantity']) for s in offer['quantity_specs']],
                         [('商品数量', 1), ('内包装数量', 1), ('商品の個数', 1)])

    def test_empty_accessibility_price_and_chinese_stock(self):
        markup = product(stock='现在有货。').replace('<span class="a-offscreen">￥3,255</span>',
            '<span class="a-offscreen"></span><span aria-hidden="true">JP¥<span class="a-price-whole">5,387</span></span>')
        offer = jp.parse_offer(jp.Page(markup), ASIN)
        self.assertEqual(offer['price'], 5387)
        self.assertEqual(offer['stock'], 'in_stock')
        self.assertIn('5,387', offer['field_evidence']['price'])

    def test_continue_shopping_is_distinct_from_captcha_input(self):
        markup = '<form action="/errors_page/validateCaptcha"><button>ショッピングを続ける</button></form>'
        with self.assertRaisesRegex(jp.AmazonError, 'AMAZON_CONTINUE_SHOPPING_REQUIRED'):
            jp.validate_page(jp.Page(markup))
        with self.assertRaisesRegex(jp.AmazonError, 'AMAZON_HUMAN_VERIFICATION_REQUIRED'):
            jp.validate_page(jp.Page(markup + '<input id="captchacharacters">'))
        opener = mock.Mock(open=mock.Mock(return_value=Response(markup.encode())))
        client = jp.AmazonProductClient(opener=opener)
        self.assertEqual(client.fetch(ASIN)['category'], 'AMAZON_CONTINUE_SHOPPING_REQUIRED')
        self.assertTrue(client.fetch('B07WQGD7G1')['not_attempted'])
        self.assertEqual(opener.open.call_count, 1)

    def test_cookie_session_persists_between_clients_outside_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'cookies.txt'
            client = jp.AmazonProductClient(cookie_file=target)
            client.jar.set_cookie(http.cookiejar.Cookie(0, 'session-id', 'fixture-private', None, False,
                '.amazon.co.jp', True, True, '/', True, True, None, True, None, None, {}, False))
            client.save_cookies()
            restored = jp.AmazonProductClient(cookie_file=target)
            self.assertEqual([(c.name, c.value) for c in restored.jar], [('session-id', 'fixture-private')])
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_browser_parser_verifies_country_and_child_asin(self):
        result = jp.parse_browser_capture({'html': product()}, ASIN)
        self.assertEqual(result['collection_method'], 'browser_headless')
        self.assertEqual(result['data']['price'], 3255)
        with self.assertRaisesRegex(jp.AmazonError, 'AMAZON_PRODUCT_NOT_CONFIRMED'):
            jp.parse_browser_capture({'html': product(asin='B07WQGD7G1')}, ASIN)
        with self.assertRaisesRegex(jp.AmazonError, 'AMAZON_DESTINATION_NOT_CONFIRMED'):
            jp.parse_browser_capture({'html': product(label='中国')}, ASIN)

    def test_existing_japan_delivery_skips_location_api_and_reload(self):
        for label in ['日本', 'Japan']:
            opener = mock.Mock()
            opener.open.side_effect = [Response(product(label=label).encode())]
            result = jp.AmazonProductClient(opener=opener, sleep=lambda _: None).fetch(ASIN)
            self.assertEqual(result['status'], 'ok')
            self.assertTrue(result['delivery_destination']['verified'])
            self.assertEqual(result['delivery_destination']['selection_method'], 'existing_session')
            self.assertEqual(opener.open.call_count, 1)
            self.assertEqual(opener.open.call_args.args[0].get_method(), 'GET')

    def test_batch_reuses_japan_but_reselects_if_country_changes(self):
        other = 'B07WQGD7G1'
        opener = flow(final=product(asin=other))
        opener.open.side_effect = [Response(product().encode()), Response(product(asin=other).encode())] + [
            Response(product(label='中国', asin=other).encode()),
            Response(b'<script>var config = {CSRF_TOKEN: "fixture-update-token"};</script>'),
            Response(json.dumps({'sembuUpdated': 1, 'address': {'countryCode': 'JP'}}).encode()),
            Response(product(asin=other).encode())]
        client = jp.AmazonProductClient(opener=opener, sleep=lambda _: None)
        for asin in [ASIN, other, other]:
            self.assertEqual(client.fetch(asin)['status'], 'ok')
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(sum(r.get_method() == 'POST' for r in requests), 1)
        self.assertEqual(len(requests), 6)

    def test_selects_japan_without_postcode_and_reloads_before_reading_offer(self):
        opener = flow()
        client = jp.AmazonProductClient(opener=opener, sleep=lambda _: None)
        result = client.fetch(ASIN)
        requests = [call.args[0] for call in opener.open.call_args_list]
        payload = json.loads(requests[2].data)
        self.assertEqual(payload['countryCode'], 'JP')
        self.assertEqual(payload['locationType'], 'COUNTRY')
        self.assertNotIn('zipCode', payload)
        self.assertEqual(requests[0].full_url, requests[3].full_url)
        self.assertTrue(result['delivery_destination']['verified'])
        self.assertIsNone(result['delivery_destination']['postal_code'])
        self.assertEqual(result['data']['price'], 3255)
        self.assertEqual(result['data']['reference_price'], 5170)
        self.assertEqual(result['data']['discount_percent'], 37)
        self.assertTrue(result['data']['buy_box'])
        self.assertEqual(result['data']['stock'], 'in_stock')
        self.assertNotIn('private-fixture', json.dumps(result))

    def test_country_response_must_match_and_refreshed_page_must_confirm_it(self):
        for kwargs in [{'address': {'countryCode': 'US'}}, {'final': product(label='中国')},
                       {'final': product(label='東京 100-0001', kind='LOCATION_INPUT')}]:
            opener = flow(**kwargs)
            result = jp.AmazonProductClient(opener=opener, sleep=lambda _: None).fetch(ASIN)
            self.assertEqual(result['category'], 'AMAZON_DESTINATION_NOT_CONFIRMED')
            self.assertFalse(result['delivery_destination']['verified'])
            self.assertNotIn('data', result)

    def test_server_resolved_domestic_zip_is_verified_without_inventing_one(self):
        opener = flow(final=product(label='東京都 100-0001', kind='LOCATION_INPUT'),
                      address={'countryCode': 'JP', 'locationType': 'LOCATION_INPUT', 'zipCode': '1000001'})
        result = jp.AmazonProductClient(opener=opener, sleep=lambda _: None).fetch(ASIN)
        self.assertTrue(result['delivery_destination']['verified'])
        self.assertEqual(result['delivery_destination']['resolved_postal_code'], '1000001')
        self.assertNotIn('zipCode', json.loads(opener.open.call_args_list[2].args[0].data))

    def test_verification_page_stops_before_location_post_and_later_asins(self):
        opener = mock.Mock()
        opener.open.return_value = Response(b'<title>Amazon.co.jp</title><form action="/errors_page/validateCaptcha"></form>')
        client = jp.AmazonProductClient(opener=opener, sleep=lambda _: None)
        for asin in [ASIN, 'B07WQGD7G1']:
            result = client.fetch(asin)
            self.assertEqual(result['category'], 'AMAZON_HUMAN_VERIFICATION_REQUIRED')
        self.assertEqual(opener.open.call_count, 1)
        self.assertTrue(result['not_attempted'])

    def test_challenge_evidence_identifies_the_failed_request_stage(self):
        opener = mock.Mock()
        challenge = b'<form action="/errors_page/validateCaptcha"></form>'
        opener.open.side_effect = [Response(challenge)]
        result = jp.AmazonProductClient(opener=opener).fetch(ASIN)
        self.assertEqual(result['stage'], 'initial_product')

        opener.open.side_effect = [Response(product(label='中国').encode()), Response(challenge)]
        result = jp.AmazonProductClient(opener=opener, sleep=lambda _: None).fetch(ASIN)
        self.assertEqual(result['stage'], 'destination_setup')

    def test_cannot_ship_is_not_out_of_stock_and_missing_offer_is_not_absent_cart(self):
        page = jp.Page(product(stock='選択した場所に配送できません。').replace('<input id="add-to-cart-button" type="submit">', ''))
        result = jp.parse_offer(page, ASIN)
        self.assertIsNone(result['stock'])
        self.assertIsNone(result['buy_box'])
        result = jp.parse_offer(jp.Page(product(stock='一時的に在庫切れ', buy_button=False)), ASIN)
        self.assertEqual(result['stock'], 'out_of_stock')
        self.assertFalse(result['buy_box'])

    def test_wrong_asin_is_not_a_confirmed_offer(self):
        with self.assertRaisesRegex(jp.AmazonError, 'AMAZON_PRODUCT_NOT_CONFIRMED'):
            jp.parse_offer(jp.Page(product(asin='B07WQGD7G1')), ASIN)


if __name__ == '__main__':
    unittest.main()
