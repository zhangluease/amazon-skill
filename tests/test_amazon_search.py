import importlib.util
import html
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


def page(asins=('B000000001',), more=False, destination='New York 10001', modal=None, destination_type='LOCATION_INPUT'):
    cards = ''.join('<div data-component-type="s-search-result" data-asin="' + a + '"></div>' for a in asins)
    return ('<title>Amazon.com : pet house</title><input id="twotabsearchtextbox" value="pet house">'
            '<span id="glow-ingress-line2">' + destination + '</span>'
            + '<input id="glowDestinationType" value="' + destination_type + '">'
            + ('<span id="nav-global-location-data-modal-action" data-a-modal="'
               + html.escape(json.dumps(modal), quote=True) + '"></span>' if modal else '')
            + '<div class="s-main-slot">' + cards + '</div>'
            + ('<a class="s-pagination-next" href="/s?k=pet+house&page=2">Next</a>' if more else ''))


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AmazonSearchTests(unittest.TestCase):
    def country_flow(self, country='US', final_destination=None, response_country=None, final_type=None):
        opener = mock.Mock()
        modal = {'url': '/portal-migration/hz/glow/get-rendered-address-selections?storeContext=NoStoreName',
                 'ajaxHeaders': {'anti-csrftoken-a2z': 'fixture-modal-token'}}
        label, location_type, postal = ('Lebanon 66952', 'LOCATION_INPUT', '66952') if country == 'US' else (
            {'JP': 'Japan', 'CA': 'Canada'}.get(country, 'Japan'), 'COUNTRY', None)
        opener.open.side_effect = [
            Response(page(('B000000099',), destination='Japan', modal=modal, destination_type='COUNTRY').encode()),
            Response(b'<script>var config = { CSRF_TOKEN : "fixture-update-token" };</script>'
                     b'<select id="GLUXCountryList"><option value="JP">Japan</option><option value="CA">Canada</option></select>'),
            Response(json.dumps({'sembuUpdated': 1, 'address': {'countryCode': response_country or country,
                'locationType': 'COUNTRY', 'zipCode': postal}}).encode()),
            Response(page(destination=final_destination if final_destination is not None else label,
                          destination_type=final_type or location_type).encode())]
        return opener

    def location_flow(self, postal_code='10001', final_destination=None, valid=True):
        opener = mock.Mock()
        modal = {'url': '/portal-migration/hz/glow/get-rendered-address-selections?deviceType=desktop&storeContext=NoStoreName',
                 'ajaxHeaders': {'anti-csrftoken-a2z': 'fixture-modal-token'}}
        opener.open.side_effect = [
            Response(page(('B000000099',), destination='Japan', modal=modal).encode()),
            Response(b'<script>var config = { CSRF_TOKEN : "fixture-update-token" };</script>'),
            Response(json.dumps({'isValidAddress': int(valid), 'isAddressUpdated': int(valid),
                                 'sembuUpdated': int(valid), 'successful': int(valid)}).encode()),
            Response(page(destination=final_destination if final_destination is not None else 'US city ' + postal_code).encode())]
        return opener

    def test_extracts_unique_main_cards_and_ignores_carousels_and_script_text(self):
        text = page(('B000000001', 'B000000001', 'bad', 'B000000002'))
        text += '<div data-asin="B000000099"></div><script>"No results for pet house"</script>'
        parsed = search.parse_page(text, 'pet house')
        self.assertEqual(parsed['asins'], ['B000000001', 'B000000002'])
        self.assertFalse(parsed['explicit_empty'])

    def test_ordinary_get_uses_us_search_and_no_login_cookie(self):
        opener = mock.Mock()
        opener.open.return_value = Response(page().encode())
        result = search.search('pet house', opener=opener, postal_code='10001')
        request = opener.open.call_args.args[0]
        url = urlsplit(request.full_url)
        self.assertEqual(url.netloc, 'www.amazon.com')
        self.assertEqual(url.path, '/s')
        self.assertEqual(parse_qs(url.query), {'k': ['pet house']})
        self.assertNotIn('cookie', {k.lower() for k in request.headers})
        self.assertEqual(result['asins_found'], 1)
        self.assertFalse(result['browser_started'])
        self.assertFalse(result['authenticated'])
        self.assertEqual(result['delivery_destination'], {
            'country_code': 'US', 'country_name': 'United States', 'postal_code': '10001', 'selection_type': 'postal_code',
            'resolved_postal_code': '10001', 'resolved_location_type': 'LOCATION_INPUT',
            'verified': True, 'page_label': 'New York 10001'})
        self.assertIsNone(search.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org'))

    def test_changes_deliver_to_and_discards_candidates_from_initial_foreign_destination(self):
        opener = self.location_flow()
        result = search.search('pet house', opener=opener, postal_code='10001')
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(opener.open.call_count, 4)
        self.assertEqual(requests[0].full_url, requests[3].full_url)
        self.assertEqual(requests[1].get_header('Anti-csrftoken-a2z'), 'fixture-modal-token')
        update = requests[2]
        self.assertEqual(urlsplit(update.full_url).path, '/portal-migration/hz/glow/address-change')
        self.assertEqual(parse_qs(urlsplit(update.full_url).query), {'actionSource': ['glow']})
        self.assertEqual(update.get_method(), 'POST')
        self.assertEqual(update.get_header('Content-type'), 'application/json')
        self.assertEqual(update.get_header('Anti-csrftoken-a2z'), 'fixture-update-token')
        self.assertEqual(json.loads(update.data), {
            'locationType': 'LOCATION_INPUT', 'zipCode': '10001', 'storeContext': 'NoStoreName',
            'deviceType': 'web', 'pageType': 'Search', 'actionSource': 'glow'})
        self.assertEqual(result['asins'], ['B000000001'])
        self.assertEqual(result['delivery_context'], 'us_postal_code_verified')
        self.assertNotIn('fixture-', json.dumps(result))
        self.assertEqual(result['pages'][0]['destination_label'], 'US city 10001')

    def test_supports_other_us_postal_codes_and_validates_before_network_access(self):
        opener = self.location_flow('90001')
        result = search.search('pet house', opener=opener, postal_code='90001')
        self.assertEqual(result['delivery_destination']['postal_code'], '90001')
        self.assertEqual(json.loads(opener.open.call_args_list[2].args[0].data)['zipCode'], '90001')
        for invalid in ['', 'US', '10001-1234', '１０００１', '10001\n', 10001]:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(search.SearchError, '^INVALID_US_POSTAL_CODE$'):
                search.search('pet house', opener=opener, postal_code=invalid)
        self.assertEqual(opener.open.call_count, 4)

    def test_destination_change_rejection_or_unverified_result_stops(self):
        for valid, label, error, count in [(False, 'New York 10001', 'AMAZON_DESTINATION_REJECTED', 3),
                                            (True, 'Japan', 'AMAZON_DESTINATION_NOT_CONFIRMED', 4),
                                            (True, '', 'AMAZON_DESTINATION_NOT_CONFIRMED', 4),
                                            (True, 'US city 210001', 'AMAZON_DESTINATION_NOT_CONFIRMED', 4)]:
            opener = self.location_flow(final_destination=label, valid=valid)
            with self.subTest(label=label, valid=valid), self.assertRaisesRegex(search.SearchError, '^' + error + '$'):
                search.search('pet house', opener=opener, postal_code='10001')
            self.assertEqual(opener.open.call_count, count)

    def test_missing_location_controls_or_cross_site_modal_cannot_be_treated_as_us(self):
        for modal in [None, {'url': 'https://example.org/', 'ajaxHeaders': {'anti-csrftoken-a2z': 'fixture'}}]:
            opener = mock.Mock()
            opener.open.return_value = Response(page(destination='Japan', modal=modal).encode())
            with self.subTest(modal=modal), self.assertRaisesRegex(search.SearchError, '^AMAZON_DESTINATION_NOT_CONFIRMED$'):
                search.search('pet house', opener=opener)
            self.assertEqual(opener.open.call_count, 1)

    def test_location_modal_challenge_stops_without_posting_or_retrying(self):
        opener = self.location_flow()
        responses = list(opener.open.side_effect)
        responses[1] = Response(b'<title>Robot Check</title>')
        opener.open.side_effect = responses
        with self.assertRaisesRegex(search.SearchError, '^AMAZON_HUMAN_VERIFICATION_REQUIRED$'):
            search.search('pet house', opener=opener)
        self.assertEqual(opener.open.call_count, 2)

    def test_each_page_requires_the_same_destination_even_when_empty(self):
        opener = mock.Mock()
        opener.open.side_effect = [Response(page(more=True).encode()),
                                   Response((page((), destination='Japan') + '<p>No results for pet house</p>').encode())]
        with self.assertRaisesRegex(search.SearchError, '^AMAZON_DESTINATION_NOT_CONFIRMED$'):
            search.search('pet house', pages=2, opener=opener, postal_code='10001')
        self.assertEqual(opener.open.call_count, 2)

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
        result = search.search('pet house', pages=4, opener=opener, postal_code='10001')
        self.assertEqual(result['asins'], ['B000000002', 'B000000001', 'B000000003'])
        self.assertEqual(len(result['pages']), 2)
        self.assertEqual(opener.open.call_count, 2)
        with self.assertRaisesRegex(search.SearchError, '^INVALID_PAGE_COUNT$'):
            search.search('pet house', pages=5, opener=opener)

    def test_default_requests_us_country_without_supplying_a_zip_and_records_amazon_resolved_location(self):
        opener = self.country_flow()
        result = search.search('pet house', opener=opener)
        payload = json.loads(opener.open.call_args_list[2].args[0].data)
        self.assertEqual(payload['locationType'], 'COUNTRY')
        self.assertEqual(payload['countryCode'], 'US')
        self.assertEqual(payload['district'], 'US')
        self.assertNotIn('zipCode', payload)
        destination = result['delivery_destination']
        self.assertEqual(destination['country_code'], 'US')
        self.assertIsNone(destination['postal_code'])
        self.assertEqual(destination['selection_type'], 'country')
        self.assertEqual(destination['resolved_postal_code'], '66952')
        self.assertEqual(destination['page_label'], 'Lebanon 66952')
        self.assertEqual(result['delivery_context'], 'country_verified')
        self.assertEqual(result['asins'], ['B000000001'])

    def test_country_changes_destination_while_search_marketplace_stays_us(self):
        for country, label in [('JP', 'Japan'), ('ca', 'Canada')]:
            opener = self.country_flow(country.upper())
            result = search.search('pet house', opener=opener, country_code=country)
            payload = json.loads(opener.open.call_args_list[2].args[0].data)
            self.assertEqual(payload['locationType'], 'COUNTRY')
            self.assertEqual(payload['countryCode'], country.upper())
            self.assertNotIn('zipCode', payload)
            self.assertEqual(result['marketplace'], 'US')
            self.assertEqual(result['delivery_destination']['country_code'], country.upper())
            self.assertIsNone(result['delivery_destination']['postal_code'])
            self.assertEqual(result['delivery_destination']['page_label'], label)
            self.assertTrue(all(urlsplit(call.args[0].full_url).netloc == 'www.amazon.com'
                                for call in opener.open.call_args_list))

    def test_country_validation_rejects_conflicting_zip_and_unsupported_countries(self):
        opener = mock.Mock()
        for country in ['', 'Japan', 'US\n', '1A', None]:
            with self.subTest(country=country), self.assertRaisesRegex(search.SearchError, '^INVALID_DESTINATION_COUNTRY$'):
                search.search('pet house', opener=opener, country_code=country)
        with self.assertRaisesRegex(search.SearchError, '^INVALID_DESTINATION_COMBINATION$'):
            search.search('pet house', opener=opener, country_code='JP', postal_code='10001')
        opener.open.assert_not_called()
        opener = self.country_flow('ZZ')
        with self.assertRaisesRegex(search.SearchError, '^AMAZON_DESTINATION_COUNTRY_UNSUPPORTED$'):
            search.search('pet house', opener=opener, country_code='ZZ')
        self.assertEqual(opener.open.call_count, 2)

    def test_country_response_and_every_search_page_must_confirm_the_destination(self):
        for changes, count in [({'response_country': 'CA'}, 3), ({'final_destination': 'Canada'}, 4),
                               ({'final_type': 'LOCATION_INPUT'}, 4)]:
            opener = self.country_flow('JP', **changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(search.SearchError, '^AMAZON_DESTINATION_NOT_CONFIRMED$'):
                search.search('pet house', opener=opener, country_code='JP')
            self.assertEqual(opener.open.call_count, count)
        opener = self.country_flow('JP')
        responses = list(opener.open.side_effect)
        responses[3] = Response(page(destination='Japan', destination_type='COUNTRY', more=True).encode())
        responses.append(Response((page((), destination='Canada', destination_type='COUNTRY')
                                   + '<p>No results for pet house</p>').encode()))
        opener.open.side_effect = responses
        with self.assertRaisesRegex(search.SearchError, '^AMAZON_DESTINATION_NOT_CONFIRMED$'):
            search.search('pet house', pages=2, opener=opener, country_code='JP')
        self.assertEqual(opener.open.call_count, 5)

    def test_us_country_auto_location_needs_server_zip_evidence_and_rejects_another_zip(self):
        for missing_zip in [False, True]:
            opener = self.country_flow('US', final_destination='New York 10001')
            if missing_zip:
                responses = list(opener.open.side_effect)
                responses[2] = Response(json.dumps({'sembuUpdated': 1,
                    'address': {'countryCode': 'US', 'locationType': 'COUNTRY'}}).encode())
                opener.open.side_effect = responses
            with self.subTest(missing_zip=missing_zip), self.assertRaisesRegex(search.SearchError, '^AMAZON_DESTINATION_NOT_CONFIRMED$'):
                search.search('pet house', opener=opener)


if __name__ == '__main__':
    unittest.main()
