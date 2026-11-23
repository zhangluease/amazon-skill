#!/usr/bin/env python3
"""Anonymous Amazon JP HTTP session: reuse verified Japan delivery or select it."""
import datetime as dt
import hashlib
from html.parser import HTMLParser
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit

ORIGIN = 'https://www.amazon.co.jp'
STOP_ERRORS = {'AMAZON_HUMAN_VERIFICATION_REQUIRED', 'AMAZON_HTTP_403', 'AMAZON_HTTP_429',
               'AMAZON_DESTINATION_NOT_CONFIRMED', 'AMAZON_DESTINATION_REJECTED'}


class AmazonError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Element:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.nodes()

    def text(self):
        if self.tag in ('script', 'style'):
            return ''
        return ' '.join(' '.join(c.text() if isinstance(c, Element) else c for c in self.children).split())

    def by_id(self, name):
        return next((n for n in self.nodes() if n.attrs.get('id') == name), None)

    def classes(self, name):
        return [n for n in self.nodes() if name in n.attrs.get('class', '').split()]


class Page(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Element()
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def by_id(self, name):
        return self.root.by_id(name)

    def destination(self):
        label, kind = self.by_id('glow-ingress-line2'), self.by_id('glowDestinationType')
        return {'page_label': label.text() if label else '',
                'resolved_location_type': kind.attrs.get('value', '') if kind else ''}


def validate_page(page):
    nodes = list(page.root.nodes())
    if (any(n.tag == 'form' and re.search('captcha', n.attrs.get('action', ''), re.I) for n in nodes)
            or page.by_id('captchacharacters') is not None
            or any(n.tag == 'title' and n.text().lower() == 'robot check' for n in nodes)):
        raise AmazonError('AMAZON_HUMAN_VERIFICATION_REQUIRED')


def amount(text):
    match = re.search(r'(?:[￥¥]\s*)?(\d[\d,]*(?:\.\d+)?)', text or '')
    return float(match[1].replace(',', '')) if match else None


def parse_offer(page, asin):
    identity = page.by_id('ASIN')
    if identity is None or identity.attrs.get('value', '').upper() != asin or page.by_id('productTitle') is None:
        raise AmazonError('AMAZON_PRODUCT_NOT_CONFIRMED')
    result = {'asin': asin, 'title': page.by_id('productTitle').text(), 'price': None,
              'reference_price': None, 'discount_percent': None, 'buy_box': None, 'stock': None,
              'reference_price_label': None, 'availability_text': None, 'currency': 'JPY', 'field_evidence': {}}
    core = page.by_id('corePriceDisplay_desktop_feature_div') or page.by_id('corePrice_feature_div')
    if core:
        price_nodes = core.classes('priceToPay')
        for node in price_nodes:
            prices = node.classes('a-offscreen')
            value = amount(prices[0].text()) if prices else None
            if value is not None:
                result['price'] = value
                result['field_evidence']['price'] = 'priceToPay: ' + prices[0].text()
                break
        bases = core.classes('basisPrice')
        for node in bases:
            prices = node.classes('a-offscreen')
            value = amount(prices[0].text()) if prices else None
            if value is not None:
                result['reference_price'] = value
                result['reference_price_label'] = node.text()
                result['field_evidence']['reference_price'] = node.text()
                break
        for node in core.classes('savingsPercentage'):
            match = re.fullmatch(r'\s*[-−]?\s*(\d+(?:\.\d+)?)\s*%\s*', node.text())
            if match and 0 <= float(match[1]) <= 100:
                result['discount_percent'] = float(match[1])
                result['field_evidence']['discount_percent'] = node.text()
                break
    if result['price'] is None:
        for key in ('priceblock_ourprice', 'priceblock_dealprice', 'price_inside_buybox'):
            node = page.by_id(key)
            if node is not None and amount(node.text()) is not None:
                result['price'] = amount(node.text())
                result['field_evidence']['price'] = key + ': ' + node.text()
                break
    for key in ('add-to-cart-button', 'add-to-cart-button-ubb'):
        button = page.by_id(key)
        if button is not None:
            result['buy_box'] = not ('disabled' in button.attrs or button.attrs.get('aria-disabled') == 'true')
            result['field_evidence']['buy_box'] = key + (' enabled' if result['buy_box'] else ' disabled')
            break
    buybox = page.by_id('buybox') or page.by_id('desktop_buybox')
    if result['buy_box'] is None and buybox is not None and re.search(
            r'現在おすすめの出品はありません|すべての購入オプションを見る|No featured offers available', buybox.text(), re.I):
        result['buy_box'] = False
        result['field_evidence']['buy_box'] = buybox.text()[:1000]
    availability = page.by_id('availability')
    if availability is not None:
        text = availability.text()
        result['availability_text'] = text
        if re.search(r'在庫切れ|在庫なし|お取り扱いできません|Currently unavailable|Out of stock', text, re.I):
            result['stock'] = 'out_of_stock'
        elif re.search(r'在庫あり|残り\s*\d+\s*点|In Stock', text, re.I):
            result['stock'] = 'in_stock'
        if result['stock'] is not None:
            result['field_evidence']['stock'] = text
    return result


class AmazonProductClient:
    def __init__(self, opener=None, interval=1, sleep=time.sleep):
        self.opener = opener or urllib.request.build_opener(NoRedirect(),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.interval, self.sleep, self.last = interval, sleep, None
        self.expected, self.blocked = None, None

    def request(self, url, headers=None, data=None):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'www.amazon.co.jp':
            raise AmazonError('AMAZON_ENDPOINT_NOT_ALLOWED')
        if self.last is not None:
            self.sleep(max(0, self.interval - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        request = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0',
            'Accept-Language': 'ja-JP,ja;q=0.9', 'Accept': 'text/html', 'Accept-Encoding': 'identity', **(headers or {})})
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            raise AmazonError('AMAZON_HTTP_' + str(error.code)) from None
        except (OSError, TimeoutError):
            raise AmazonError('AMAZON_NETWORK_FAILED') from None
        if len(body) > 8 * 1024 * 1024:
            raise AmazonError('AMAZON_RESPONSE_TOO_LARGE')
        text = body.decode('utf-8', 'replace')
        page = Page(text)
        validate_page(page)
        return text, page, hashlib.sha256(body).hexdigest()

    def select_japan(self, page, referer):
        node = page.by_id('nav-global-location-data-modal-action')
        try:
            modal = json.loads(node.attrs['data-a-modal'])
            relative = modal['url']
            token = modal['ajaxHeaders']['anti-csrftoken-a2z']
            if not isinstance(relative, str) or not relative.startswith('/') or relative.startswith('//'):
                raise ValueError()
            if not isinstance(token, str) or not token or len(token) > 4096 or re.search(r'[\r\n]', token):
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AmazonError('AMAZON_DESTINATION_NOT_CONFIRMED') from None
        text, _, _ = self.request(ORIGIN + relative, {'anti-csrftoken-a2z': token,
            'Referer': referer, 'X-Requested-With': 'XMLHttpRequest'})
        match = re.search(r'\bCSRF_TOKEN\s*:\s*("(?:\\.|[^"\\])*")', text)
        try:
            token = json.loads(match[1]) if match else None
            if not isinstance(token, str) or not token or len(token) > 4096 or re.search(r'[\r\n]', token):
                raise ValueError()
        except (ValueError, TypeError):
            raise AmazonError('AMAZON_DESTINATION_NOT_CONFIRMED') from None
        store = parse_qs(urlsplit(relative).query).get('storeContext', ['generic'])[0]
        payload = {'storeContext': store, 'deviceType': 'web', 'pageType': 'Detail', 'actionSource': 'glow',
                   'locationType': 'COUNTRY', 'district': 'JP', 'countryCode': 'JP'}
        text, _, _ = self.request(ORIGIN + '/portal-migration/hz/glow/address-change?actionSource=glow',
            {'anti-csrftoken-a2z': token, 'Referer': referer, 'X-Requested-With': 'XMLHttpRequest',
             'Content-Type': 'application/json', 'Accept': 'application/json'}, json.dumps(payload).encode())
        try:
            data = json.loads(text)
            if data.get('sembuUpdated') != 1:
                raise AmazonError('AMAZON_DESTINATION_REJECTED')
            address = data.get('address', {})
            if address.get('countryCode') != 'JP':
                raise ValueError()
            self.expected = {'country_code': 'JP', 'country_name': '日本', 'selection_type': 'country',
                'postal_code': None, 'resolved_postal_code': address.get('zipCode'), 'verified': False,
                'selection_method': 'country_change_api'}
        except (ValueError, TypeError, AttributeError):
            raise AmazonError('AMAZON_DESTINATION_NOT_CONFIRMED') from None

    def verify_japan(self, page):
        state = page.destination()
        label, kind = state['page_label'], state['resolved_location_type']
        expected = self.expected
        confirmed = False
        if expected is None and kind == 'COUNTRY' and label.strip().casefold() in ('日本', 'japan', '日本国'):
            # An explicit existing delivery country is enough; no location POST or reload is needed.
            expected = self.expected = {'country_code': 'JP', 'country_name': '日本', 'selection_type': 'country',
                'postal_code': None, 'resolved_postal_code': None, 'selection_method': 'existing_session', 'verified': False}
        if expected and kind == 'COUNTRY':
            confirmed = label.strip().casefold() in ('日本', 'japan', '日本国')
        if expected and kind == 'LOCATION_INPUT':
            postal = str(expected.get('resolved_postal_code') or '')
            if re.fullmatch(r'\d{3}-?\d{4}', postal):
                confirmed = re.sub(r'\D', '', postal) in re.sub(r'\D', '', label)
        if not confirmed:
            raise AmazonError('AMAZON_DESTINATION_NOT_CONFIRMED')
        return dict(expected, **state, verified=True)

    def fetch(self, asin):
        url = ORIGIN + '/dp/' + asin
        if not re.fullmatch(r'[A-Z0-9]{10}', asin):
            raise AmazonError('INVALID_ASIN')
        if self.blocked:
            return {'status': 'failed', 'category': self.blocked, 'not_attempted': True,
                    'delivery_destination': {'country_code': 'JP', 'verified': False}}
        stage = 'initial_product'
        try:
            _, page, digest = self.request(url)
            try:
                destination = self.verify_japan(page)
            except AmazonError:
                stage = 'destination_setup'
                self.select_japan(page, url)
                stage = 'product_refresh'
                _, page, digest = self.request(url)
                stage = 'destination_verification'
                destination = self.verify_japan(page)
            stage = 'offer_extraction'
            offer = parse_offer(page, asin)
            return {'status': 'ok', 'endpoint': url, 'collected_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                    'delivery_destination': destination, 'body_sha256': digest, 'data': offer,
                    'collection_method': 'anonymous_http', 'browser_started': False}
        except AmazonError as error:
            category = str(error)
            if category in STOP_ERRORS:
                self.blocked = category
            return {'status': 'failed', 'endpoint': url, 'category': category, 'stage': stage,
                    'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                    'delivery_destination': {'country_code': 'JP', 'verified': False},
                    'collection_method': 'anonymous_http', 'browser_started': False}
