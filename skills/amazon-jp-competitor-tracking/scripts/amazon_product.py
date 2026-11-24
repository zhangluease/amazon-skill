#!/usr/bin/env python3
"""Anonymous Amazon JP HTTP session: reuse verified Japan delivery or select it."""
import datetime as dt
import hashlib
from html.parser import HTMLParser
import http.cookiejar
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit

ORIGIN = 'https://www.amazon.co.jp'
STOP_ERRORS = {'AMAZON_HUMAN_VERIFICATION_REQUIRED', 'AMAZON_CONTINUE_SHOPPING_REQUIRED',
               'AMAZON_HTTP_403', 'AMAZON_HTTP_429',
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
    captcha_form = any(n.tag == 'form' and re.search('captcha', n.attrs.get('action', ''), re.I) for n in nodes)
    if (page.by_id('captchacharacters') is not None
            or any(n.tag == 'title' and n.text().lower() == 'robot check' for n in nodes)):
        raise AmazonError('AMAZON_HUMAN_VERIFICATION_REQUIRED')
    if captcha_form:
        if re.search(r'ショッピングを続け|Continue shopping|继续购物|繼續購物', page.root.text(), re.I):
            raise AmazonError('AMAZON_CONTINUE_SHOPPING_REQUIRED')
        raise AmazonError('AMAZON_HUMAN_VERIFICATION_REQUIRED')


def amount(text):
    match = re.search(r'(?:[￥¥]\s*)?(\d[\d,]*(?:\.\d+)?)', text or '')
    return float(match[1].replace(',', '')) if match else None


def price_amount(node):
    """Some layouts leave a-offscreen empty and put the amount in a-price-whole."""
    for child in node.classes('a-offscreen'):
        value = amount(child.text())
        if value is not None:
            return value, child.text()
    whole = node.classes('a-price-whole')
    if whole:
        fraction = node.classes('a-price-fraction')
        text = whole[0].text().rstrip('.')
        if fraction and fraction[0].text():
            text += '.' + fraction[0].text()
        return amount(text), node.text()
    return None, None


ITEM_COUNT_LABELS = {'商品数量', '商品數量', '商品个数', '商品個数', '商品の個数', '商品数', '個数', '点数',
                     'ユニット数', 'ユニットカウント', 'number of items', 'unit count'}
PACK_COUNT_LABELS = {'内包装数量', '內包裝數量', '包装数量', '包裝數量', '入数', '入り数', 'セット内容数',
                     '商品パッケージ数', 'パッケージ数量', '梱包数', '商品パッケージの数量', 'item package quantity'}
QUANTITY_SPEC_SECTIONS = ('productOverview_feature_div', 'productFactsDesktop_feature_div',
                          'productFactsDesktopExpander', 'productDetails_techSpec_section_1',
                          'productDetails_techSpec_section_2', 'productDetails_detailBullets_sections1',
                          'detailBullets_feature_div')
INVISIBLE_MARKS = re.compile(r'[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]')


def quantity_specs(rows):
    """Keep only explicit count fields and whole-number unit values."""
    result = []
    if not isinstance(rows, list):
        return result
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('label'), str)
                or not isinstance(row.get('value'), str)):
            continue
        label, value = (INVISIBLE_MARKS.sub('', unicodedata.normalize('NFKC', row[key])).strip()
                        for key in ('label', 'value'))
        label = label.strip(' :')
        key = label.casefold()
        if key not in ITEM_COUNT_LABELS | PACK_COUNT_LABELS:
            continue
        match = re.fullmatch(r'([1-9][0-9]{0,3})(?:\.0+)?\s*'
                             r'(?:[個个枚本袋点件](?:入り|入|装)?|セット|パック|pcs?|pieces?|counts?|units?)?', value, re.I)
        if match:
            field = {'label': label, 'value': value, 'quantity': int(match[1]),
                     'kind': 'item' if key in ITEM_COUNT_LABELS else 'package'}
            if field not in result:
                result.append(field)
    return result


def product_quantity_specs(page):
    rows = []
    for section_id in QUANTITY_SPEC_SECTIONS:
        section = page.by_id(section_id)
        if section is None:
            continue
        for node in section.nodes():
            if node.tag == 'tr':
                cells = [child for child in node.children if isinstance(child, Element) and child.tag in ('th', 'td')]
                if len(cells) == 2:
                    rows.append({'label': cells[0].text(), 'value': cells[1].text()})
            elif node.tag == 'li' and section_id == 'detailBullets_feature_div':
                fields = re.split(r'[:：]', node.text(), maxsplit=1)
                if len(fields) == 2:
                    rows.append({'label': fields[0], 'value': fields[1]})
    return quantity_specs(rows)


def parse_offer(page, asin):
    identity = page.by_id('ASIN')
    if identity is None or identity.attrs.get('value', '').upper() != asin or page.by_id('productTitle') is None:
        raise AmazonError('AMAZON_PRODUCT_NOT_CONFIRMED')
    result = {'asin': asin, 'title': page.by_id('productTitle').text(), 'price': None,
              'reference_price': None, 'discount_percent': None, 'buy_box': None, 'stock': None,
              'reference_price_label': None, 'availability_text': None, 'currency': 'JPY', 'field_evidence': {}}
    result['quantity_specs'] = product_quantity_specs(page)
    image = page.by_id('landingImage')
    if image is not None:
        result['main_image'] = image.attrs.get('data-old-hires') or image.attrs.get('src')
    core = page.by_id('corePriceDisplay_desktop_feature_div') or page.by_id('corePrice_feature_div')
    if core:
        price_nodes = core.classes('priceToPay')
        for node in price_nodes:
            value, label = price_amount(node)
            if value is not None:
                result['price'] = value
                result['field_evidence']['price'] = 'priceToPay: ' + label
                break
        bases = core.classes('basisPrice')
        for node in bases:
            value, _ = price_amount(node)
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
        if re.search(r'在庫切れ|在庫なし|お取り扱いできません|缺货|无库存|暫時無貨|目前无货|Currently unavailable|Out of stock', text, re.I):
            result['stock'] = 'out_of_stock'
        elif re.search(r'在庫あり|残り\s*\d+\s*点|现在有货|現有存貨|有库存|库存仅剩\s*\d+\s*件|In Stock', text, re.I):
            result['stock'] = 'in_stock'
        if result['stock'] is not None:
            result['field_evidence']['stock'] = text
    return result


class AmazonProductClient:
    def __init__(self, opener=None, interval=1, sleep=time.sleep, cookie_file=None):
        self.cookie_file = Path(cookie_file) if cookie_file and opener is None else None
        self.jar = http.cookiejar.MozillaCookieJar(str(self.cookie_file)) if self.cookie_file else http.cookiejar.CookieJar()
        if self.cookie_file and self.cookie_file.exists():
            if self.cookie_file.is_symlink():
                raise AmazonError('AMAZON_UNSAFE_SESSION_FILE')
            try:
                self.jar.load(ignore_discard=True)
            except (OSError, http.cookiejar.LoadError):
                raise AmazonError('AMAZON_SESSION_INVALID') from None
        self.opener = opener or urllib.request.build_opener(NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.jar))
        self.interval, self.sleep, self.last = interval, sleep, None
        self.expected, self.blocked = None, None

    def save_cookies(self):
        if not self.cookie_file:
            return
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix='.amazon-cookies-', dir=self.cookie_file.parent)
        os.close(fd)
        try:
            self.jar.save(temporary, ignore_discard=True)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.cookie_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

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
        try:
            self.save_cookies()
        except OSError:
            raise AmazonError('AMAZON_SESSION_SAVE_FAILED') from None
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


def parse_browser_capture(payload, asin):
    """Private browser bridge input; output only public offer fields, never HTML/tokens."""
    if not re.fullmatch(r'[A-Z0-9]{10}', asin):
        raise AmazonError('INVALID_ASIN')
    html = payload.get('html')
    if not isinstance(html, str) or len(html.encode('utf-8')) > 8 * 1024 * 1024:
        raise AmazonError('AMAZON_RESPONSE_TOO_LARGE')
    page = Page(html)
    validate_page(page)
    client = AmazonProductClient()
    expected = payload.get('expected_destination')
    if expected is not None:
        if not isinstance(expected, dict) or expected.get('country_code') != 'JP':
            raise AmazonError('AMAZON_DESTINATION_NOT_CONFIRMED')
        client.expected = {key: expected.get(key) for key in ('country_code', 'country_name', 'selection_type',
            'postal_code', 'resolved_postal_code', 'selection_method', 'verified')}
    destination = client.verify_japan(page)
    return {'status': 'ok', 'endpoint': ORIGIN + '/dp/' + asin,
            'collected_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'delivery_destination': destination, 'data': parse_offer(page, asin),
            'body_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest(),
            'collection_method': 'browser_' + ('headed' if payload.get('headed') else 'headless'),
            'browser_started': True}


if __name__ == '__main__':
    import sys
    try:
        if len(sys.argv) != 3 or sys.argv[1] != 'parse-browser':
            raise AmazonError('INVALID_ARGUMENT')
        raw = sys.stdin.buffer.read(9 * 1024 * 1024 + 1)
        if len(raw) > 9 * 1024 * 1024:
            raise AmazonError('AMAZON_RESPONSE_TOO_LARGE')
        result = parse_browser_capture(json.loads(raw), sys.argv[2])
    except (AmazonError, ValueError, TypeError, AttributeError) as error:
        result = {'status': 'failed', 'category': str(error) if isinstance(error, AmazonError) else 'INVALID_BROWSER_CAPTURE'}
    print(json.dumps(result, ensure_ascii=False))
