#!/usr/bin/env python3
"""Read US Amazon search result ASINs with anonymous HTTP; never launch a browser."""
import argparse
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode


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

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
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
        if tag == 'title':
            self.in_title = False
        if tag in ['script', 'style'] and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if not self.skip:
            self.visible.append(data)


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
    return {'asins': parser.asins, 'explicit_empty': empty, 'next_page': next_page}


def search(query, pages=1, opener=None):
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise SearchError('INVALID_QUERY')
    if not isinstance(pages, int) or not 1 <= pages <= 4:
        raise SearchError('INVALID_PAGE_COUNT')
    query = query.strip()
    opener = opener or urllib.request.build_opener(NoRedirect())
    asins, records = [], []
    for page in range(1, pages + 1):
        url = 'https://www.amazon.com/s?' + urlencode({'k': query, 'page': page})
        request = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html', 'Accept-Language': 'en-US,en;q=0.9'})
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
        parsed = parse_page(body.decode('utf-8', 'replace'), query)
        for asin in parsed['asins']:
            if asin not in asins:
                asins.append(asin)
        if len(asins) > 200:
            raise SearchError('AMAZON_CANDIDATE_LIMIT_EXCEEDED')
        records.append({'page': page, 'url': url, 'http_status': status,
                        'asins_found': len(parsed['asins']), 'body_bytes': len(body),
                        'body_sha256': hashlib.sha256(body).hexdigest(),
                        'elapsed_ms': round((time.monotonic() - started) * 1000)})
        if parsed['explicit_empty'] or not parsed['next_page']:
            break
    return {'format': 'amazon-search-asins-v1', 'status': 'ok' if asins else 'empty',
            'query': query, 'marketplace': 'US', 'candidate_source': 'amazon_anonymous_search_http',
            'queried_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'asins': asins,
            'pages_requested': pages, 'pages': records, 'asins_found': len(asins),
            'authenticated': False, 'cookies_supplied': False, 'browser_started': False,
            'delivery_context': 'anonymous_default', 'scope': 'returned_main_search_cards',
            'explicit_empty': not asins}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', required=True)
    parser.add_argument('--pages', type=int, default=1)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = search(args.query, args.pages)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'status': result['status'], 'asins_found': result['asins_found'],
                          'pages_loaded': len(result['pages']), 'browser_started': False}))
        return 0
    except (SearchError, OSError) as error:
        category = str(error) if isinstance(error, SearchError) else 'CHECK_OUTPUT_PATH'
        print(json.dumps({'status': 'failed', 'category': category}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
