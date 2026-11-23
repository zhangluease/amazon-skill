"""Shared input, session validation and fixed SellerSprite HTTP transport (stdlib only)."""
import csv
import datetime as dt
import io
import json
import math
import os
from pathlib import Path
import re
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request

JST = dt.timezone(dt.timedelta(hours=9))


ORIGIN = 'https://e.sellersprite.com'


AUTH_ERRORS = {'MISSING_HTTP_SESSION', 'INVALID_HTTP_SESSION', 'HTTP_401',
               'ERR_NEED_AUTHORIZED', 'ERR_NEED_RE_AUTHORIZED', 'ERROR_UNAUTHORIZED',
               'ERR_NEED_RENEWAL_AUTHORIZED'}


STOP_ERRORS = {'HTTP_429', 'HTTP_403', 'ERR_GLOBAL_403', 'ERR_FREQUENT', 'ERR_VERSION_NEED_UPGRADE'}


HEADERS = ('auth-token', 'auth-fp', 'random-token', 'user-agent')


ASIN_RE = re.compile(r'[A-Z0-9]{10}')


PRODUCT_KEYS = ('asin', 'parent', 'title', 'image', 'image_zoom', 'brand', 'price', 'currency',
                'symbol', 'rating', 'reviews', 'bsr', 'bsr_id', 'bsr_label', 'bsrList', 'bsr_list',
                'units', 'month_units', 'available', 'created_time', 'unavailable', 'seller_id',
                'seller_name', 'site_postcode', 'site_language', 'node_label_path', 'first_available_day')


CHANGE_KEYS = ('timePoint', 'changeType', 'previousTitle', 'currentTitle',
               'previousHighlight', 'currentHighlight',
               'previousImages', 'currentImages', 'previousFeatures', 'currentFeatures')


CHANGE_SCHEMA = 'listing-title-highlight-v2'


OFFER_FIELDS = ('price', 'reference_price', 'discount_percent', 'buy_box', 'stock')


BROWSER_OFFER_SOURCE = 'amazon_jp_browser_offer'


class ApiError(Exception):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and re.fullmatch(r'[A-Z0-9_]{1,80}', code) else 'API_FAILED'
        super().__init__(self.code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_output_directory(output, result_file, *, resume=False):
    output = Path(output)
    if not output.exists():
        return
    if not output.is_dir():
        raise ApiError('OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY')
    resuming = resume and (output / result_file).is_file()
    for name in ('products.json', 'changes.json', 'report.html'):
        if (output / name).exists() and not (resuming and name in (result_file, 'report.html')):
            raise ApiError('OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY')
    evidence = output / 'evidence'
    if evidence.exists() and (not evidence.is_dir() or any(evidence.iterdir())) and not resuming:
        raise ApiError('OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY')


def parse_asin(value):
    if not isinstance(value, str):
        raise ApiError('INVALID_ASIN_INPUT')
    value = value.strip()
    if ASIN_RE.fullmatch(value.upper()):
        return value.upper()
    try:
        u = urllib.parse.urlsplit(value)
        if u.scheme != 'https' or u.hostname not in ('www.amazon.co.jp', 'amazon.co.jp') or u.username or u.password or u.port not in (None, 443):
            raise ValueError()
        match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:/|$)', u.path, re.I)
        if match:
            return match[1].upper()
    except ValueError:
        pass
    raise ApiError('INVALID_JP_PRODUCT_URL_OR_ASIN')


def read_inputs(values=(), input_file=None):
    rows = list(values)
    if input_file:
        p = Path(input_file)
        text = p.read_text(encoding='utf-8-sig')
        if p.suffix.lower() == '.json':
            data = json.loads(text)
            if not isinstance(data, list):
                raise ApiError('INPUT_JSON_ARRAY_REQUIRED')
            rows.extend(data)
        elif p.suffix.lower() in ('.csv', '.tsv'):
            reader = csv.DictReader(io.StringIO(text), delimiter='\t' if p.suffix.lower() == '.tsv' else ',')
            for row in reader:
                record = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
                value = record.get('asin') or record.get('url') or record.get('商品链接')
                if not value and any(record.values()):
                    raise ApiError('INPUT_ASIN_COLUMN_REQUIRED')
                if value:
                    rows.append(value)
        else:
            rows.extend(line.strip() for line in text.splitlines() if line.strip())
    normalized = []
    for row in rows:
        if isinstance(row, dict):
            row = row.get('asin') or row.get('url')
        # The CLI accepts comma-separated ASINs as well as space-separated arguments.
        if isinstance(row, str) and not row.strip().startswith('https://'):
            normalized.extend(parse_asin(item) for item in re.split(r'[,，\s]+', row.strip()) if item)
        else:
            normalized.append(parse_asin(row))
    asins = list(dict.fromkeys(normalized))
    if not 1 <= len(asins) <= 200:
        raise ApiError('ASIN_COUNT_MUST_BE_1_TO_200')
    return asins


def window(as_of=None):
    if as_of:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', as_of):
            day = dt.date.fromisoformat(as_of)
            end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), JST) - dt.timedelta(milliseconds=1)
        else:
            end = dt.datetime.fromisoformat(as_of.replace('Z', '+00:00'))
            if end.tzinfo is None:
                raise ApiError('AS_OF_TIMEZONE_REQUIRED')
            end = end.astimezone(JST)
    else:
        end = dt.datetime.now(JST)
    start = dt.datetime.combine(end.date() - dt.timedelta(days=6), dt.time(), JST)
    return {'start': start.isoformat(), 'end': end.isoformat(), 'start_ms': int(start.timestamp() * 1000),
            'end_ms': int(end.timestamp() * 1000), 'timezone': 'Asia/Tokyo',
            'basis': 'seven_calendar_dates_including_end_date', 'provider_timestamp_timezone': 'unspecified'}


def history_window(as_of=None, all_history=False):
    scope = window(as_of)
    if all_history:
        scope.update(start='1970-01-01T00:00:00+00:00', start_ms=0,
                     basis='all_available_history_through_end')
    return scope


def load_session(path):
    try:
        p = Path(path).expanduser()
        if not p.is_file():
            raise ApiError('MISSING_HTTP_SESSION')
        if p.stat().st_size > 32768:
            raise ValueError()
        data = json.loads(p.read_text(encoding='utf-8-sig'))
        version = data['extension_version']
        if data.get('format') != 'sellersprite-http-session-v1' or not re.fullmatch(r'\d+(?:\.\d+){2,3}', version):
            raise ValueError()
        headers = {}
        for name in HEADERS:
            value = data['headers'].get(name)
            if value is not None:
                if not isinstance(value, str) or not value or len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                    raise ValueError()
                headers[name] = value
        if any(headers.get(k, '').lower() in ('', 'null', 'undefined', '***', '[redacted]') for k in HEADERS[:3]):
            raise ValueError()
        parameters = {k: v for k, v in data.get('parameters', {}).items()
                      if k in ('version', 'language', 'extension', 'source') and isinstance(v, str) and len(v) <= 200}
        parameters['version'] = version
        return {'headers': headers, 'parameters': parameters, 'extension_version': version}
    except ApiError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ApiError('INVALID_HTTP_SESSION') from None


def checksum(value, version):
    seed = int(version.replace('.', '00', 1).replace('.', '0'))
    def mix(number, pattern):
        for i in range(0, len(pattern) - 2, 3):
            shift = int(pattern[i + 2], 36)
            shifted = number >> shift if pattern[i + 1] == '+' else number << shift
            number = (number + shifted if pattern[i] == '+' else number ^ shifted) & 0xffffffff
        return number
    number = seed
    for byte in value.encode('utf-8'):
        number = mix(number + byte, '+-a^+6')
    number = (mix(number, '+-3^+b+-f') ^ 1364508470) % 1000000
    return str(number) + '.' + str(number ^ seed)


class Client:
    def __init__(self, session, interval=1, opener=None, sleep=time.sleep):
        self.session, self.interval = session, interval
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.sleep, self.last = sleep, None

    def get(self, route, params):
        if not re.fullmatch(r'/v2/extension/(?:asin/JP|competitor-lookup/JP|keepa|chart/amz-unit-trend/variations|'
                            r'listing/JP/(?:asin-change|ads-trend)/[A-Z0-9]{10})', route):
            raise ApiError('ENDPOINT_NOT_ALLOWED')
        if self.last is not None:
            self.sleep(max(0, self.interval - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        query = urllib.parse.urlencode(dict(self.session['parameters'], **params))
        req = urllib.request.Request(ORIGIN + route + '?' + query, headers=dict(self.session['headers'],
            **{'accept': 'application/json', 'content-type': 'application/json', 'accept-encoding': 'identity'}))
        try:
            with self.opener.open(req, timeout=30) as response:
                data = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            raise ApiError('HTTP_' + str(error.code)) from None
        except (OSError, TimeoutError):
            raise ApiError('NETWORK_FAILED') from None
        if len(data) > 8 * 1024 * 1024:
            raise ApiError('RESPONSE_TOO_LARGE')
        try:
            document = json.loads(data, parse_constant=lambda _: None)
        except (ValueError, UnicodeError):
            raise ApiError('NON_JSON_RESPONSE') from None
        if not isinstance(document, dict):
            raise ApiError('INVALID_RESPONSE')
        if document.get('code') != 'OK':
            raise ApiError(document.get('code'))
        if 'data' not in document:
            raise ApiError('MISSING_DATA')
        return document['data']


def scalar(value):
    if isinstance(value, str):
        return value[:10000]
    if type(value) in (int, float) and math.isfinite(value):
        return value
    return None


def number(value):
    # Preserve threshold strings such as 100+ as text in their original column.
    if isinstance(value, str) and re.fullmatch(r'\d+(?:,\d{3})*(?:\.\d+)?', value.strip()):
        value = float(value.replace(',', ''))
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return value
    return None


def purchase_threshold(value):
    """The extension renders month_units as useNumber(value, 0) + '+'."""
    if isinstance(value, str) and re.fullmatch(r'\s*\d[\d,]*\+\s*', value):
        return value.strip()
    numeric = number(value)
    if numeric is not None and float(numeric).is_integer():
        return f'{int(numeric):,}+'
    return None


def public_product(row):
    result = {key: scalar(row[key]) for key in PRODUCT_KEYS if key in row and key not in ('bsrList', 'bsr_list', 'unavailable')}
    if type(row.get('unavailable')) is bool:
        result['unavailable'] = row['unavailable']
    ranks = row.get('bsr_list') if isinstance(row.get('bsr_list'), list) else row.get('bsrList')
    if isinstance(ranks, list):
        result['bsr_list'] = [{k: scalar(r.get(k)) for k in ('rank', 'id', 'label', 'text')}
                              | ({'main': r['main']} if type(r.get('main')) is bool else {})
                              for r in ranks if isinstance(r, dict)]
    return result


def product_row(data, asin, *, sales=False):
    rows = data.get('items') if sales and isinstance(data, dict) else data
    pending = sales and isinstance(data, dict) and isinstance(data.get('crawlAsins'), list) and asin in data['crawlAsins']
    if rows is None and pending:
        raise ApiError('PRODUCT_NOT_CACHED')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ApiError('INVALID_PRODUCT_RESPONSE')
    found = [row for row in rows if row.get('asin') == asin]
    if len(found) > 1:
        raise ApiError('DUPLICATE_PRODUCT_RESPONSE')
    if not found:
        raise ApiError('PRODUCT_NOT_CACHED' if pending else 'ASIN_NOT_RETURNED')
    return public_product(found[0])


def safe_image(value):
    if not isinstance(value, str):
        return None
    try:
        u = urllib.parse.urlsplit(value)
        if u.scheme == 'https' and u.hostname in ('m.media-amazon.com', 'images-na.ssl-images-amazon.com',
            'images-fe.ssl-images-amazon.com', 'images-eu.ssl-images-amazon.com') and not u.username and not u.password and u.port in (None, 443):
            return urllib.parse.urlunsplit(('https', u.hostname, u.path, '', ''))
    except ValueError:
        pass
    return None
