"""Shared input, session validation and fixed SellerSprite HTTP transport (stdlib only)."""
import csv
import datetime as dt
from email.utils import parsedate_to_datetime
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
import unicodedata

JST = dt.timezone(dt.timedelta(hours=9))


ORIGIN = 'https://e.sellersprite.com'


AUTH_ERRORS = {'MISSING_HTTP_SESSION', 'INVALID_HTTP_SESSION', 'HTTP_401',
               'ERR_NEED_AUTHORIZED', 'ERR_NEED_RE_AUTHORIZED', 'ERROR_UNAUTHORIZED',
               'ERR_NEED_RENEWAL_AUTHORIZED', 'PLUGIN_LOGIN_REQUIRED'}


RATE_LIMIT_ERRORS = {'HTTP_429', 'ERR_FREQUENT', 'PROVIDER_RATE_LIMIT_PENDING'}
STOP_ERRORS = RATE_LIMIT_ERRORS | {'HTTP_403', 'ERR_GLOBAL_403', 'ERR_VERSION_NEED_UPGRADE',
               'ERR_ROBOT_CHECK', 'PROVIDER_VERIFICATION_PENDING', 'PROVIDER_AUTHORIZATION_PENDING',
               'PROVIDER_BUSY'}


HEADERS = ('auth-token', 'auth-fp', 'random-token', 'user-agent')


ASIN_RE = re.compile(r'[A-Z0-9]{10}')


PRODUCT_KEYS = ('asin', 'parent', 'title', 'image', 'image_zoom', 'brand', 'price', 'currency',
                'symbol', 'rating', 'reviews', 'bsr', 'bsr_id', 'bsr_label', 'bsrList', 'bsr_list',
                'units', 'month_units', 'available', 'created_time', 'unavailable', 'seller_id',
                'seller_name', 'site_postcode', 'site_language', 'node_label_path', 'first_available_day',
                'variations', 'variation_list')


CHANGE_SCHEMA = 'listing-media-changes-v3'


OFFER_FIELDS = ('price', 'reference_price', 'discount_percent', 'buy_box', 'stock')


BROWSER_OFFER_SOURCE = 'amazon_jp_browser_offer'


class ApiError(Exception):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and re.fullmatch(r'[A-Z0-9_]{1,80}', code) else 'API_FAILED'
        super().__init__(self.code)


def retry_after_seconds(value):
    try:
        if value and re.fullmatch(r'\d{1,10}', value.strip()):
            return int(value)
        when = parsedate_to_datetime(value)
        if when.tzinfo is not None:
            return max(0, when.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError, OSError):
        pass


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
        if any(headers.get(k, '').lower() in ('', 'null', 'undefined', '[redacted]')
               or is_login_mask(headers.get(k)) for k in HEADERS[:3]):
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


def request_parameters(session, route, params):
    """Mirror the original extension's common request middleware, not only sales."""
    values = {key: value for key, value in params.items() if value is not None}
    if not values.get('tk'):
        text = values.get('asins') or values.get('asin') or values.get('q') or route
        values['tk'] = checksum(str(text), session['extension_version'])
    values.update(session['parameters'])
    return values


class Client:
    def __init__(self, session, interval=1, opener=None, sleep=time.sleep, session_file=None, gate=None):
        if gate is None:
            from competitor_coordination import ProviderGate
            gate = ProviderGate()
        self.session, self.interval = session, interval
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.sleep, self.last = sleep, None
        self.session_file = Path(session_file).expanduser() if session_file else None
        self.renewal_attempted, self.auth_events = False, []
        self.missing_session_checked = False
        self.request_trace = []
        self.gate = gate

    def _reload_session(self):
        if self.session_file and self.session_file.exists():
            self.session = load_session(self.session_file)

    def _pause_on_error(self, error):
        if error.code in ('HTTP_429', 'ERR_FREQUENT'):
            delay = getattr(error, 'retry_after', None)
            self.gate.pause(error.code, 'rate_limit', delay=1800 if delay is None else max(1, delay))
        elif error.code == 'ERR_ROBOT_CHECK':
            self.gate.pause(error.code, 'verification')
        elif error.code in AUTH_ERRORS:
            self.gate.pause(error.code, 'authorization')

    def _request(self, origin, route, params):
        # Fixed vendor origins only; redirects cannot forward the authorized headers.
        if not (origin == ORIGIN and re.fullmatch(
                r'/v2/extension/(?:client/latest|asin/JP|competitor-lookup/JP|keepa|chart/amz-unit-trend/variations|'
                r'listing/JP/(?:asin-change|ads-trend)/[A-Z0-9]{10})', route)
                or origin == 'https://www.sellersprite.com' and route == '/v2/extension/tk/signin'):
            raise ApiError('ENDPOINT_NOT_ALLOWED')
        if self.last is not None:
            self.sleep(max(0, self.interval - (time.monotonic() - self.last)))
        self.gate.pace(self.interval)
        self.last = time.monotonic()
        parameters = request_parameters(self.session, route, params)
        query = urllib.parse.urlencode(parameters)
        trace = {'started_at': dt.datetime.now(JST).isoformat(), 'origin': origin,
                 'route': route, 'parameter_names': sorted(parameters), 'signed': bool(parameters.get('tk'))}
        self.request_trace.append(trace)
        req = urllib.request.Request(origin + route + '?' + query, headers=dict(self.session['headers'],
            **{'accept': 'application/json', 'content-type': 'application/json', 'accept-encoding': 'identity'}))
        try:
            with self.opener.open(req, timeout=30) as response:
                data = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            trace['status'] = 'HTTP_' + str(error.code)
            failure = ApiError(trace['status'])
            if error.code == 429 and error.headers:
                failure.retry_after = retry_after_seconds(error.headers.get('Retry-After'))
            error.close()
            raise failure from None
        except (OSError, TimeoutError):
            trace['status'] = 'NETWORK_FAILED'
            raise ApiError('NETWORK_FAILED') from None
        finally:
            trace['finished_at'] = dt.datetime.now(JST).isoformat()
        if len(data) > 8 * 1024 * 1024:
            raise ApiError('RESPONSE_TOO_LARGE')
        try:
            document = json.loads(data, parse_constant=lambda _: None)
        except (ValueError, UnicodeError):
            raise ApiError('NON_JSON_RESPONSE') from None
        if not isinstance(document, dict):
            raise ApiError('INVALID_RESPONSE')
        trace['status'] = 'OK' if document.get('code') == 'OK' else ApiError(document.get('code')).code
        if document.get('code') != 'OK':
            raise ApiError(document.get('code'))
        if 'data' not in document:
            raise ApiError('MISSING_DATA')
        if origin == ORIGIN and snapshot_login_masked(route, params, document['data']):
            error = ApiError('PLUGIN_LOGIN_REQUIRED')
            error.snapshot_data = document['data']
            raise error
        return document['data']

    def renew_session(self, reason):
        """Same token sign-in as the plugin's account button; at most once per worker."""
        with self.gate.hold():
            self._reload_session()
            try:
                return self._renew_session(reason)
            except ApiError as error:
                self._pause_on_error(error)
                raise

    def _renew_session(self, reason):
        if self.renewal_attempted:
            raise ApiError('ERR_NEED_RE_AUTHORIZED')
        self.renewal_attempted = True
        event = {'action': 'token_signin', 'reason': reason, 'status': 'failed'}
        self.auth_events.append(event)
        try:
            data = self._request('https://www.sellersprite.com', '/v2/extension/tk/signin', {})
            token = data.get('token') if isinstance(data, dict) else None
            if (not isinstance(token, str) or not token or len(token) > 8192
                    or token.lower() in ('null', 'undefined', '[redacted]') or is_login_mask(token)
                    or any(ord(c) < 32 or ord(c) == 127 for c in token)):
                raise ApiError('INVALID_TOKEN_SIGNIN_RESPONSE')
            if data.get('guest') is True:
                raise ApiError('ERR_NEED_AUTHORIZED')
            self.session['headers']['auth-token'] = token
            if self.session_file:
                write_json(self.session_file, dict(self.session, format='sellersprite-http-session-v1',
                           captured_at=dt.datetime.now(JST).isoformat()))
            event['status'] = 'ok'
        except ApiError as error:
            event['code'] = error.code
            raise

    def get(self, route, params):
        with self.gate.hold():
            self._reload_session()
            try:
                return self._get(route, params)
            except ApiError as error:
                self._pause_on_error(error)
                raise

    def _get(self, route, params):
        try:
            data = self._request(ORIGIN, route, params)
        except ApiError as error:
            if error.code not in AUTH_ERRORS or self.renewal_attempted:
                raise
            renewal_completed = False
            try:
                self.renew_session(error.code)
                renewal_completed = True
                return self._request(ORIGIN, route, params)
            except ApiError as recovery_error:
                if not renewal_completed and recovery_error.code == 'ERR_ACCOUNT_NOT_EXIST':
                    # A rejected token sign-in needs interactive plugin login. The renewal event
                    # already records the vendor's original code.
                    recovery_error = ApiError('ERR_NEED_RE_AUTHORIZED')
                if hasattr(error, 'snapshot_data') and not hasattr(recovery_error, 'snapshot_data'):
                    recovery_error.snapshot_data = error.snapshot_data
                raise recovery_error from None
        if not self.missing_session_checked and missing_snapshot_data(route, params, data):
            self.missing_session_checked = True
            try:
                checked = self.verify_session('missing_snapshot_data')
                if checked['token_rotated']:
                    return self._request(ORIGIN, route, params)
            except ApiError as error:
                # The original successful snapshot still contains useful business data.
                # Callers must sanitize it and requery this source after authorization.
                error.snapshot_data = data
                raise
        return data

    def verify_session(self, reason='explicit_session_check'):
        with self.gate.hold(allow_paused=True):
            self._reload_session()
            try:
                result = self._verify_session(reason)
                self.gate.verified()
                return result
            except ApiError as error:
                self._pause_on_error(error)
                raise

    def _verify_session(self, reason):
        event = {'action': 'session_check', 'reason': reason, 'status': 'failed'}
        self.auth_events.append(event)
        previous = self.session['headers']['auth-token']
        try:
            data = self.get('/v2/extension/client/latest', {})
            token = data.get('token') if isinstance(data, dict) else None
            if isinstance(data, dict) and data.get('guest') is True:
                raise ApiError('ERR_NEED_AUTHORIZED')
            if (not isinstance(token, str) or not token or len(token) > 8192
                    or token.lower() in ('null', 'undefined', '[redacted]') or is_login_mask(token)
                    or any(ord(c) < 32 or ord(c) == 127 for c in token)):
                raise ApiError('INVALID_SESSION_CHECK_RESPONSE')
            self.session['headers']['auth-token'] = token
            if self.session_file and token != previous:
                write_json(self.session_file, dict(self.session, format='sellersprite-http-session-v1',
                           captured_at=dt.datetime.now(JST).isoformat()))
            event['status'] = 'ok'
            return {'authenticated': True, 'token_rotated': token != previous}
        except ApiError as error:
            event['code'] = error.code
            raise


def is_login_mask(value):
    """The plugin's all-asterisk placeholder indicates that login is required."""
    return isinstance(value, str) and re.fullmatch(r'[*＊]{3,}', value.strip()) is not None


def snapshot_login_masked(route, params, data):
    if is_login_mask(data):
        return True
    if route not in ('/v2/extension/asin/JP', '/v2/extension/competitor-lookup/JP'):
        return False
    asins = params.get('asins')
    if not isinstance(asins, str):
        return False
    requested = {value for value in asins.split(',') if ASIN_RE.fullmatch(value)}
    rows = data.get('items') if isinstance(data, dict) else data
    if is_login_mask(rows):
        return True
    return isinstance(rows, list) and any(
        isinstance(row, dict) and row.get('asin') in requested
        and any(is_login_mask(row.get(key)) for key in PRODUCT_KEYS) for row in rows)


def missing_snapshot_data(route, params, data):
    """Sparse product/sales snapshots trigger one login check, never empty history."""
    if route not in ('/v2/extension/asin/JP', '/v2/extension/competitor-lookup/JP'):
        return False
    asin = params.get('asins')
    if not isinstance(asin, str) or not ASIN_RE.fullmatch(asin):
        return False
    sales = route.endswith('competitor-lookup/JP')
    try:
        row = product_row(data, asin, sales=sales)
    except ApiError as error:
        return error.code in ('ASIN_NOT_RETURNED', 'PRODUCT_NOT_CACHED')
    if sales:
        return number(row.get('units')) is None or purchase_threshold(row.get('month_units')) is None
    return (any(not row.get(key) for key in ('title', 'brand'))
            or not (row.get('image') or row.get('image_zoom'))
            or any(number(row.get(key)) is None for key in ('bsr', 'rating', 'reviews')))


def scalar(value):
    if isinstance(value, str):
        return None if is_login_mask(value) else value[:10000]
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


def package_quantity_mentions(title):
    text = unicodedata.normalize('NFKC', title) if isinstance(title, str) else ''
    matches = re.findall(
        r'(?<![0-9])(?<![0-9][.,])([1-9][0-9]{0,3})\s*'
        r'(?:枚|個|个|本(?!体)|袋|セット|組(?![合み])|パック|套|件|片装|入(?!手)り?|[-‐‑– ]?packs?\b)'
        r'(?!当たり|あたり|目)'
        r'|\bpack\s+of\s+([1-9][0-9]{0,3})(?![0-9.,])', text, re.I)
    return [int(counter or pack) for counter, pack in matches]


def declared_package_quantity(title):
    """Use an explicit single title quantity; mixed component counts stay unknown."""
    matches = package_quantity_mentions(title)
    return matches[0] if len(matches) == 1 else None


def package_quantity_candidate(product):
    image = safe_image(product.get('main_image'))
    if (product.get('title') and image and product.get('package_quantity') is None
            and not product.get('package_quantity_conflict')
            and not package_quantity_mentions(product.get('package_quantity_title', product['title']))):
        return {'asin': product['asin'], 'main_image': image}


def apply_package_image_review(product, review):
    """Accept a visual count only for the exact ASIN and primary image reviewed."""
    candidate = package_quantity_candidate(product)
    if (not isinstance(review, dict) or not candidate
            or any(review.get(key) != value for key, value in candidate.items())):
        raise ApiError('PACKAGE_REVIEW_PRODUCT_OR_IMAGE_MISMATCH')
    quantity, evidence = review.get('quantity'), review.get('evidence')
    if quantity is None:
        return False
    if type(quantity) is not int or not 1 <= quantity <= 9999 or not isinstance(evidence, str) or not evidence.strip():
        raise ApiError('INVALID_PACKAGE_IMAGE_REVIEW')
    product['package_quantity'] = quantity
    product['package_quantity_evidence'] = {'main_image': candidate['main_image'], 'observation': evidence.strip()[:500]}
    product['field_sources']['package_quantity'] = 'amazon_jp_main_image_review'
    product['missing_fields'] = [key for key in product.get('missing_fields', []) if key != 'package_quantity']
    if (product['status'] == 'partial' and not product['missing_fields'] and not product['errors']
            and product.get('history', {}).get('status') == 'ok'):
        product['status'] = 'ok'
    return True


def title_original_claim(title):
    """Report the listing's explicit claim, without inferring physical authenticity."""
    text = title or ''
    if re.search(r'互換|非純正|純正品では(?:ありません|ない)|compatible|non[- ]?original', text, re.I):
        return False
    if re.search(r'純正|正規品|【正品】', text):
        return True
    return None


def public_product(row):
    result = {key: scalar(row[key]) for key in PRODUCT_KEYS if key in row and key not in ('bsrList', 'bsr_list', 'unavailable')}
    if type(row.get('unavailable')) is bool:
        result['unavailable'] = row['unavailable']
    if isinstance(row.get('variation_list'), list):
        result['variation_list'] = [{'asin': scalar(item.get('asin')), 'attr': scalar(item.get('attr'))}
                                    for item in row['variation_list'] if isinstance(item, dict)]
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
