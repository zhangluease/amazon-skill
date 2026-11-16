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


DEFAULT_US_ZIP = '10001'


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

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
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


def change_destination(opener, modal, referer, postal_code):
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
        headers['anti-csrftoken-a2z'] = json.loads(match.group(1))
    except ValueError:
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED') from None
    if (not headers['anti-csrftoken-a2z'] or len(headers['anti-csrftoken-a2z']) > 4096
            or re.search(r'[\r\n]', headers['anti-csrftoken-a2z'])):
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
    headers.update({'Content-Type': 'application/json', 'Accept': 'application/json'})
    store = parse_qs(urlsplit(modal_url).query).get('storeContext', ['generic'])[0]
    data = json.dumps({'locationType': 'LOCATION_INPUT', 'zipCode': postal_code,
                       'storeContext': store, 'deviceType': 'web',
                       'pageType': 'Search', 'actionSource': 'glow'}).encode('utf-8')
    body, _, _ = request_page(opener, 'https://www.amazon.com/portal-migration/hz/glow/address-change?actionSource=glow', headers, data)
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED') from None
    if not isinstance(result, dict) or 'sembuUpdated' not in result:
        raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
    if result['sembuUpdated'] != 1:
        raise SearchError('AMAZON_DESTINATION_REJECTED')


def search(query, pages=1, opener=None, postal_code=DEFAULT_US_ZIP):
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise SearchError('INVALID_QUERY')
    if not isinstance(pages, int) or not 1 <= pages <= 4:
        raise SearchError('INVALID_PAGE_COUNT')
    if not isinstance(postal_code, str) or not re.fullmatch(r'[0-9]{5}', postal_code):
        raise SearchError('INVALID_US_POSTAL_CODE')
    query = query.strip()
    opener = opener or urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    first_url = 'https://www.amazon.com/s?' + urlencode({'k': query})
    first_response = request_page(opener, first_url)
    initial = parse_page(first_response[0].decode('utf-8', 'replace'), query)
    if not destination_matches(initial['destination_label'], postal_code):
        change_destination(opener, initial['location_modal'], first_url, postal_code)
        first_response = None
    asins, records = [], []
    for page in range(1, pages + 1):
        url = first_url if page == 1 else 'https://www.amazon.com/s?' + urlencode({'k': query, 'page': page})
        body, status, elapsed_ms = first_response if page == 1 and first_response else request_page(opener, url)
        parsed = parse_page(body.decode('utf-8', 'replace'), query)
        if not destination_matches(parsed['destination_label'], postal_code):
            raise SearchError('AMAZON_DESTINATION_NOT_CONFIRMED')
        for asin in parsed['asins']:
            if asin not in asins:
                asins.append(asin)
        if len(asins) > 200:
            raise SearchError('AMAZON_CANDIDATE_LIMIT_EXCEEDED')
        records.append({'page': page, 'url': url, 'http_status': status,
                        'asins_found': len(parsed['asins']), 'body_bytes': len(body),
                        'body_sha256': hashlib.sha256(body).hexdigest(),
                        'elapsed_ms': elapsed_ms, 'destination_label': parsed['destination_label']})
        if parsed['explicit_empty'] or not parsed['next_page']:
            break
    return {'format': 'amazon-search-asins-v1', 'status': 'ok' if asins else 'empty',
            'query': query, 'marketplace': 'US', 'candidate_source': 'amazon_anonymous_search_http',
            'queried_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'asins': asins,
            'pages_requested': pages, 'pages': records, 'asins_found': len(asins),
            'authenticated': False, 'cookies_supplied': False, 'browser_started': False,
            'delivery_context': 'us_postal_code_verified',
            'delivery_destination': {'country_code': 'US', 'postal_code': postal_code,
                                     'verified': True, 'page_label': records[0]['destination_label']},
            'scope': 'returned_main_search_cards',
            'explicit_empty': not asins}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', required=True)
    parser.add_argument('--pages', type=int, default=1)
    parser.add_argument('--amazon-zip', default=DEFAULT_US_ZIP, help='US Deliver to postal code (default: 10001)')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = search(args.query, args.pages, postal_code=args.amazon_zip)
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
