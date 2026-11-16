#!/usr/bin/env python3
"""Read US Amazon search result ASINs with anonymous HTTP; never launch a browser."""
import argparse
import datetime as dt
import hashlib
import http.cookiejar
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit


DEFAULT_COUNTRY = 'US'


class SearchError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SearchParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.asins, self.visible, self.links = [], [], []
        self.title, self.in_title, self.skip = '', False, 0
        self.search_value, self.search_landmark, self.captcha = None, False, False
        self.destination_parts, self.destination_depth, self.location_modal = [], 0, None
        self.destination_type = ''
        self.country_names, self.in_country_list, self.country_option = {}, False, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get('id') == 'glowDestinationType':
            self.destination_type = a.get('value', '')
        if tag == 'select' and a.get('id') == 'GLUXCountryList':
            self.in_country_list = True
        if tag == 'option' and self.in_country_list:
            self.country_option = [a.get('value', ''), []]
        if tag == 'span':
            if self.destination_depth:
                self.destination_depth += 1
            elif a.get('id') == 'glow-ingress-line2':
                self.destination_depth = 1
        if a.get('id') == 'nav-global-location-data-modal-action':
            try:
                self.location_modal = json.loads(a.get('data-a-modal', ''))
            except (ValueError, TypeError):
                pass
        if tag in ['script', 'style']:
            self.skip += 1
        if tag == 'title':
            self.in_title = True
        if 's-main-slot' in a.get('class', '').split():
            self.search_landmark = True
        if a.get('id') == 'twotabsearchtextbox':
            self.search_value = a.get('value', '')
        if tag == 'form' and re.search(r'/(?:errors/)?validateCaptcha|/captcha', a.get('action', ''), re.I):
            self.captcha = True
        if a.get('id') == 'captchacharacters':
            self.captcha = True
        asin = a.get('data-asin', '')
        if a.get('data-component-type') == 's-search-result' and re.fullmatch(r'[A-Z0-9]{10}', asin):
            if asin not in self.asins:
                self.asins.append(asin)
        if tag == 'a' and a.get('href'):
            self.links.append((a.get('class', ''), a['href']))

    def handle_endtag(self, tag):
        if tag == 'option' and self.country_option:
            code, parts = self.country_option
            if re.fullmatch(r'[A-Z]{2}', code):
                self.country_names[code] = ' '.join(''.join(parts).split())
            self.country_option = None
        if tag == 'select':
            self.in_country_list, self.country_option = False, None
        if tag == 'span' and self.destination_depth:
            self.destination_depth -= 1
        if tag == 'title':
            self.in_title = False
        if tag in ['script', 'style'] and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if not self.skip:
            self.visible.append(data)
            if self.destination_depth:
                self.destination_parts.append(data)
            if self.country_option:
                self.country_option[1].append(data)


def parse_page(text, query):
    parser = SearchParser()
    parser.feed(text)
    visible = ' '.join(parser.visible)
    title = parser.title.strip()
    if parser.captcha or title.lower() == 'robot check' or (not parser.asins and re.search(
            r'automated access to Amazon|Enter the characters you see', visible, re.I)):
        raise SearchError('AMAZON_HUMAN_VERIFICATION_REQUIRED')
    normal = lambda s: ' '.join(s.lower().split())
    if normal(query) not in normal(title) and normal(parser.search_value or '') != normal(query):
        raise SearchError('AMAZON_QUERY_NOT_CONFIRMED')
    empty = not parser.asins and parser.search_landmark and bool(re.search(
        r'No results for|did not match any products|No results matched', visible, re.I))
    if not parser.asins and not empty:
        raise SearchError('AMAZON_RESULTS_NOT_CONFIRMED')
    next_page = any('s-pagination-next' in classes.split() for classes, href in parser.links)
    return {'asins': parser.asins, 'explicit_empty': empty, 'next_page': next_page,
            'destination_label': ' '.join(''.join(parser.destination_parts).split()),
            'destination_type': parser.destination_type,
            'location_modal': parser.location_modal}


def request_page(opener, url, headers=None, data=None):
    request = urllib.request.Request(url, data=data, headers={
        'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html',
        'Accept-Language': 'en-US,en;q=0.9', **(headers or {})})
    started = time.monotonic()
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(8 * 1024 * 1024 + 1)
            status = response.status
    except urllib.error.HTTPError as error:
        raise SearchError('AMAZON_HTTP_' + str(error.code)) from None
    except (OSError, TimeoutError):
        raise SearchError('AMAZON_NETWORK_FAILED') from None
    if len(body) > 8 * 1024 * 1024:
        raise SearchError('AMAZON_RESPONSE_TOO_LARGE')
    if status != 200:
        raise SearchError('AMAZON_HTTP_' + str(status))
    # Location endpoints can return a verification page instead of HTML/JSON data.
    check = SearchParser()
    check.feed(body.decode('utf-8', 'replace'))
    if check.captcha or check.title.strip().lower() == 'robot check':
        raise SearchError('AMAZON_HUMAN_VERIFICATION_REQUIRED')
    return body, status, round((time.monotonic() - started) * 1000)


def destination_matches(label, postal_code):
    return bool(re.search(r'(?<!\d)' + re.escape(postal_code) + r'(?:-\d{4})?(?!\d)', label))


def load_location(opener, modal, referer):
    if not isinstance(modal, dict):
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
    modal_url = modal.get('url')
    if (not isinstance(modal_url, str) or not modal_url.startswith('/')
            or modal_url.startswith('//') or urlsplit(modal_url).netloc):
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
    modal_headers = modal.get('ajaxHeaders')
    token = modal_headers.get('anti-csrftoken-a2z') if isinstance(modal_headers, dict) else None
    if not isinstance(token, str) or not token or len(token) > 4096 or re.search(r'[\r\n]', token):
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
    headers = {'anti-csrftoken-a2z': token, 'Referer': referer, 'X-Requested-With': 'XMLHttpRequest'}
    body, _, _ = request_page(opener, 'https://www.amazon.com' + modal_url, headers)
    match = re.search(r'\bCSRF_TOKEN\s*:\s*("(?:\\.|[^"\\])*")', body.decode('utf-8', 'replace'))
    if not match:
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
    try:
        token = json.loads(match.group(1))
    except ValueError:
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED') from None
    if not token or len(token) > 4096 or re.search(r'[\r\n]', token):
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
    parser = SearchParser()
    parser.feed(body.decode('utf-8', 'replace'))
    return {'token': token, 'countries': parser.country_names,
            'store': parse_qs(urlsplit(modal_url).query).get('storeContext', ['generic'])[0]}


def change_destination(opener, location, referer, country_code, postal_code=None):
    country_label = location['countries'].get(country_code)
    # The US is domestic on Amazon.com and is absent from its international-country dropdown.
    if country_code == 'US':
        country_label = 'United States'
    if not country_label:
        raise SearchError('AMAZON_DESTINATION_COUNTRY_UNSUPPORTED')
    headers = {'anti-csrftoken-a2z': location['token'], 'Referer': referer,
               'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/json',
               'Accept': 'application/json'}
    payload = {'storeContext': location['store'], 'deviceType': 'web',
               'pageType': 'Search', 'actionSource': 'glow'}
    if postal_code is not None:
        payload.update({'locationType': 'LOCATION_INPUT', 'zipCode': postal_code})
    else:
        payload.update({'locationType': 'COUNTRY', 'district': country_code, 'countryCode': country_code})
    data = json.dumps(payload).encode('utf-8')
    body, _, _ = request_page(opener, 'https://www.amazon.com/portal-migration/hz/glow/address-change?actionSource=glow', headers, data)
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED') from None
    if not isinstance(result, dict) or 'sembuUpdated' not in result:
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
    if result['sembuUpdated'] != 1:
        raise SearchError('AMAZON_DESTINATION_REJECTED')
    address = result.get('address')
    if postal_code is None:
        if not isinstance(address, dict) or address.get('countryCode') != country_code:
            raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
        resolved_zip = address.get('zipCode')
        resolved_type = address.get('locationType')
        if resolved_type != 'COUNTRY' and not (country_code == 'US' and resolved_type == 'LOCATION_INPUT'):
            raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
        # The country response may stay COUNTRY while the refreshed US page resolves to LOCATION_INPUT.
        resolved_zip = (resolved_zip if country_code == 'US' and isinstance(resolved_zip, str)
                        and re.fullmatch(r'[0-9]{5}', resolved_zip) else None)
        return {'country_code': country_code, 'country_label': country_label, 'postal_code': resolved_zip}
    return {'country_label': country_label, 'location_type': 'LOCATION_INPUT', 'postal_code': postal_code}


def country_destination_matches(parsed, expected):
    if (expected['country_code'] == 'US' and parsed['destination_type'] == 'LOCATION_INPUT'
            and expected['postal_code'] is not None):
        return destination_matches(parsed['destination_label'], expected['postal_code'])
    if parsed['destination_type'] != 'COUNTRY':
        return False
    normalize = lambda text: ' '.join(re.sub(r'[\u200b-\u200f\u202a-\u202e\u2066-\u2069]', '', text).casefold().split())
    return normalize(parsed['destination_label']) == normalize(expected['country_label'])


def search(query, pages=1, opener=None, postal_code=None, country_code=DEFAULT_COUNTRY):
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise SearchError('INVALID_QUERY')
    if not isinstance(pages, int) or not 1 <= pages <= 4:
        raise SearchError('INVALID_PAGE_COUNT')
    if not isinstance(country_code, str) or not re.fullmatch(r'[A-Za-z]{2}', country_code):
        raise SearchError('INVALID_DESTINATION_COUNTRY')
    country_code = country_code.upper()
    if postal_code is not None and (not isinstance(postal_code, str) or not re.fullmatch(r'[0-9]{5}', postal_code)):
        raise SearchError('INVALID_US_POSTAL_CODE')
    if postal_code is not None and country_code != 'US':
        raise SearchError('INVALID_DESTINATION_COMBINATION')
    query = query.strip()
    opener = opener or urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    first_url = 'https://www.amazon.com/s?' + urlencode({'k': query})
    first_response = request_page(opener, first_url)
    initial = parse_page(first_response[0].decode('utf-8', 'replace'), query)
    expected = None
    if postal_code is None or not destination_matches(initial['destination_label'], postal_code):
        location = load_location(opener, initial['location_modal'], first_url)
        expected = change_destination(opener, location, first_url, country_code, postal_code)
        first_response = None
    asins, records = [], []
    for page in range(1, pages + 1):
        url = first_url if page == 1 else 'https://www.amazon.com/s?' + urlencode({'k': query, 'page': page})
        body, status, elapsed_ms = first_response if page == 1 and first_response else request_page(opener, url)
        parsed = parse_page(body.decode('utf-8', 'replace'), query)
        confirmed = (destination_matches(parsed['destination_label'], postal_code) if postal_code is not None
                     else country_destination_matches(parsed, expected))
        if not confirmed:
            raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
        for asin in parsed['asins']:
            if asin not in asins:
                asins.append(asin)
        if len(asins) > 200:
            raise SearchError('AMAZON_CANDIDATE_LIMIT_EXCEEDED')
        records.append({'page': page, 'url': url, 'http_status': status,
                        'asins_found': len(parsed['asins']), 'body_bytes': len(body),
                        'body_sha256': hashlib.sha256(body).hexdigest(),
                        'elapsed_ms': elapsed_ms, 'destination_label': parsed['destination_label'],
                        'destination_type': parsed['destination_type']})
        if parsed['explicit_empty'] or not parsed['next_page']:
            break
    return {'format': 'amazon-search-asins-v1', 'status': 'ok' if asins else 'empty',
            'query': query, 'marketplace': 'US', 'candidate_source': 'amazon_anonymous_search_http',
            'queried_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'asins': asins,
            'pages_requested': pages, 'pages': records, 'asins_found': len(asins),
            'authenticated': False, 'cookies_supplied': False, 'browser_started': False,
            'delivery_context': 'us_postal_code_verified' if postal_code is not None else 'country_verified',
            'delivery_destination': {'country_code': country_code, 'postal_code': postal_code,
                                     'country_name': expected['country_label'] if expected else 'United States',
                                     'selection_type': 'postal_code' if postal_code is not None else 'country',
                                     'resolved_postal_code': (postal_code if postal_code is not None else
                                                              expected['postal_code'] if records[0]['destination_type'] == 'LOCATION_INPUT' else None),
                                     'resolved_location_type': records[0]['destination_type'],
                                     'verified': True, 'page_label': records[0]['destination_label']},
            'scope': 'returned_main_search_cards',
            'explicit_empty': not asins}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', required=True)
    parser.add_argument('--pages', type=int, default=1)
    parser.add_argument('--amazon-country', default=DEFAULT_COUNTRY, help='Deliver to country code (default: US)')
    parser.add_argument('--amazon-zip', help='Optional US Deliver to postal code; requires country US')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = search(args.query, args.pages, postal_code=args.amazon_zip, country_code=args.amazon_country)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'status': result['status'], 'asins_found': result['asins_found'],
                          'pages_loaded': len(result['pages']), 'browser_started': False,
                          'delivery_destination': result['delivery_destination']}))
        return 0
    except (SearchError, OSError) as error:
        category = str(error) if isinstance(error, SearchError) else 'CHECK_OUTPUT_PATH'
        print(json.dumps({'status': 'failed', 'category': category}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
