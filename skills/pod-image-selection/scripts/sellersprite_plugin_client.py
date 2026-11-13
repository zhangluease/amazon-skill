#!/usr/bin/env python3
"""Query US keyword images and parent sales using a locally authorized HTTP session."""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import urllib.request

ENDPOINT = 'https://e.sellersprite.com/v2/extension/competitor-lookup/US'
ROUTE = re.compile(r'/v2/extension/competitor-lookup/[A-Z]{2}')
IMAGE_HOSTS = {'m.media-amazon.com', 'images-na.ssl-images-amazon.com',
               'images-eu.ssl-images-amazon.com', 'images-fe.ssl-images-amazon.com'}
HEADERS = {'auth-token', 'auth-fp', 'random-token', 'accept', 'content-type', 'user-agent'}


class ApiError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_session_file(path):
    if not path or not Path(path).expanduser().is_file():
        raise ApiError('MISSING_HTTP_SESSION')
    try:
        path = Path(path).expanduser()
        if path.stat().st_size > 32768:
            raise ValueError()
        value = json.loads(path.read_text(encoding='utf-8'))
        version = value.get('extension_version', '')
        if value.get('format') != 'sellersprite-http-session-v1' or not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', version):
            raise ValueError()
        source = value['headers']
        headers = {}
        for name in HEADERS:
            v = source.get(name)
            if v is not None:
                if not isinstance(v, str) or not v or len(v) > 8192 or '\r' in v or '\n' in v:
                    raise ValueError()
                headers[name] = v
        if any(headers.get(k, '').lower() in ['', 'null', 'undefined', '***', '[redacted]']
               for k in ['auth-token', 'auth-fp', 'random-token']):
            raise ValueError()
        params = value.get('parameters', {})
        params = {k: v for k, v in params.items() if k in ['version', 'language', 'extension', 'source']
                  and isinstance(v, str) and len(v) <= 200}
        params['version'] = version
        return {'headers': headers, 'parameters': params, 'extension_version': version,
                'captured_at': value.get('captured_at')}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ApiError('INVALID_HTTP_SESSION') from None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def validate_query(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError('需要 1–500 字符的关键词。')
    return query.strip()


def validate_response(document):
    if not isinstance(document, dict) or document.get('code') != 'OK':
        code = document.get('code') if isinstance(document, dict) else None
        raise ApiError(code if isinstance(code, str) and re.fullmatch(r'[A-Z0-9_]{1,80}', code) else 'API_FAILED')
    data = document.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ApiError('UNEXPECTED_RESPONSE')
    if isinstance(data.get('total'), (int, float)) and data['total'] > 0 and not data['items']:
        raise ApiError('EMPTY_PAGE_WITH_MATCHES')
    return document


def checksum(value, version):
    """The checksum used by extension 5.0.6; it is separate from session authorization."""
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
    number = ((mix(number, '+-3^+b+-f') ^ 1364508470) & 0xffffffff) % 1000000
    return str(number) + '.' + str(number ^ seed)


def load_session(path):
    """Read only the supplied file; never inspect browser profiles or login databases."""
    if not path:
        raise ApiError('MISSING_SESSION_HAR')
    try:
        path = Path(path).expanduser()
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ApiError('HAR_TOO_LARGE')
        document = json.loads(path.read_text(encoding='utf-8-sig'))
        entries = document.get('log', {}).get('entries')
        if not isinstance(entries, list):
            raise ApiError('INVALID_SESSION_HAR')
        candidates = []
        for index, entry in enumerate(entries):
            request = entry.get('request', {})
            u = urlsplit(request.get('url', ''))
            if (u.scheme != 'https' or u.hostname != 'e.sellersprite.com'
                    or u.username or u.password or u.port not in (None, 443)
                    or not ROUTE.fullmatch(u.path) or request.get('method') != 'GET'):
                continue
            timestamp = entry.get('startedDateTime', '')
            try:
                captured_at = dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp()
            except (ValueError, TypeError, AttributeError):
                captured_at = 0
            candidates.append((captured_at, index, request, u, timestamp))
        if not candidates:
            raise ApiError('MISSING_CAPTURED_MAIN_REQUEST')
        _, _, request, u, timestamp = max(candidates, key=lambda candidate: candidate[:2])
        params = dict(parse_qsl(u.query, keep_blank_values=True))
        version = params.get('version', '')
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', version):
            raise ApiError('MISSING_EXTENSION_VERSION')
        headers = {}
        for header in request.get('headers', []):
            name, value = header.get('name', '').lower(), header.get('value', '')
            if name in HEADERS and isinstance(value, str):
                if len(value) > 8192 or '\r' in value or '\n' in value:
                    raise ApiError('INVALID_SESSION_HEADER')
                headers[name] = value
        for name in ['auth-token', 'auth-fp', 'random-token']:
            if headers.get(name, '').lower() in ['', '[redacted]', 'redacted', '***', 'undefined', 'null']:
                raise ApiError('MISSING_SESSION_HEADERS')
        return {'headers': headers, 'parameters': {key: params[key] for key in
                ['version', 'language', 'extension', 'source'] if key in params},
                'captured_at': timestamp, 'extension_version': version}
    except ApiError:
        raise
    except (OSError, ValueError, TypeError, AttributeError):
        raise ApiError('INVALID_SESSION_HAR') from None


def validate_asins(asins):
    if not isinstance(asins, list) or not 1 <= len(asins) <= 200 or any(
            not isinstance(asin, str) or not re.fullmatch('[A-Z0-9]{10}', asin) for asin in asins):
        raise ApiError('INVALID_ASIN_COHORT')
    if len(set(asins)) != len(asins):
        raise ApiError('INVALID_ASIN_COHORT')
    return asins


def load_search_cohort(path, query):
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        value = json.loads(path.read_text(encoding='utf-8'))
        if (value.get('format') != 'amazon-search-asins-v1' or value.get('marketplace') != 'US'
                or value.get('query') != query or value.get('status') != 'ok'):
            raise ValueError()
        validate_asins(value.get('asins'))
        return value
    except (OSError, ValueError, TypeError, AttributeError):
        raise ApiError('INVALID_ASIN_COHORT') from None


def request_plugin(query, session, asins=None):
    query = validate_query(query)
    if asins is None:
        parameters = dict(session['parameters'], qt='K', q=query,
                          tk=checksum(query, session['extension_version']))
    else:
        joined = ','.join(validate_asins(asins))
        parameters = dict(session['parameters'], q='', asins=joined, tk=checksum(joined, session['extension_version']))
    headers = dict(session['headers'])
    headers.update({'accept-encoding': 'identity', 'accept': 'application/json', 'content-type': 'application/json'})
    request = urllib.request.Request(ENDPOINT + '?' + urlencode(parameters), headers=headers, method='GET')
    started = time.monotonic()
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            body = response.read(8 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        raise ApiError('HTTP_' + str(error.code)) from None
    except (OSError, TimeoutError):
        raise ApiError('NETWORK_FAILED') from None
    if len(body) > 8 * 1024 * 1024:
        raise ApiError('RESPONSE_TOO_LARGE')
    try:
        document = validate_response(json.loads(body))
    except ValueError:
        raise ApiError('NON_JSON_RESPONSE') from None
    market = document['data'].get('marketplace')
    if market not in ['https://www.amazon.com', 'https://www.amazon.com/', 'US']:
        raise ApiError('UNEXPECTED_MARKETPLACE')
    return document, round(1000 * (time.monotonic() - started))


def public_candidates(document):
    """Do not save raw responses or login fields, including when a server echoes them."""
    data = validate_response(document)['data']
    rows = []
    for row in data['items']:
        if not isinstance(row, dict):
            raise ApiError('UNEXPECTED_PRODUCT')
        result = {}
        for key in ['asin']:
            value = row.get(key)
            if isinstance(value, str) and re.fullmatch('[A-Z0-9]{10}', value):
                result[key] = value
        units = row.get('units')
        if isinstance(units, (int, float)) and not isinstance(units, bool) and math.isfinite(units):
            result['units'] = units
        image = row.get('image')
        if isinstance(image, str):
            try:
                u = urlsplit(image)
                if (u.scheme == 'https' and u.hostname in IMAGE_HOSTS and not u.username
                        and not u.password and u.port in (None, 443)):
                    result['imageUrl'] = urlunsplit(('https', u.hostname, u.path, '', ''))
            except ValueError:
                pass
        rows.append(result)
    total = data.get('total')
    return {'code': 'OK', 'data': {'items': rows, 'total': total if isinstance(total, int) else None}}


def normalize_plugin(query, document):
    query = validate_query(query)
    public = public_candidates(document)
    valid, incomplete, seen = [], [], set()
    for index, item in enumerate(public['data']['items']):
        asin = item.get('asin')
        if not asin:
            incomplete.append({'row': index + 1, 'reason': 'invalid_asin'})
            continue
        if asin in seen:
            continue
        seen.add(asin)
        units, image = item.get('units'), item.get('imageUrl')
        if units is None or units < 0:
            incomplete.append({'asin': asin, 'reason': 'missing_parent_sales'})
            continue
        if not image:
            incomplete.append({'asin': asin, 'reason': 'missing_image'})
            continue
        valid.append({'asin': asin,
                      'image_url': image, 'parent_units_30d': units, 'api_order': index,
                      'product_url': 'https://www.amazon.com/dp/' + asin})
    valid.sort(key=lambda item: (-item['parent_units_30d'], item['api_order']))
    for rank, item in enumerate(valid[:30], 1):
        item['rank'] = rank
    sales = [row.get('units') for row in public['data']['items']]
    valid_sales = [value for value in sales if isinstance(value, (int, float)) and value >= 0]
    result = {'query': query, 'marketplace': 'US', 'source': 'sellersprite_extension_keyword_api',
              'collection_method': 'independent_http',
              'scope': 'sellersprite_returned_keyword_candidates', 'match_type': 'extension_keyword_search',
              'sales_window': 'nearly', 'deduplication': 'asin_only',
              'sort': 'parent_units_30d_desc_then_api_order',
              'queried_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'returned_rows_complete': not incomplete, 'incomplete': incomplete,
              'total_matches': public['data']['total'], 'requested_top': 30, 'products': valid[:30],
              'returned_candidates': len(sales), 'ranking_scope': 'returned_candidates_only',
              'global_top_30_verified': False,
              'observed_server_units_desc': all(a >= b for a, b in zip(valid_sales, valid_sales[1:])),
              'note': '在本次接口返回的关键词候选中按父体近30天销量取前30；未验证全部匹配商品的全局排名。关键词匹配可能含非目标品类。'}
    return result, public


def normalize_cohort(query, document, cohort):
    requested = validate_asins(cohort['asins'])
    lookup = {}
    for row in validate_response(document)['data']['items']:
        if isinstance(row, dict) and isinstance(row.get('asin'), str) and re.fullmatch('[A-Z0-9]{10}', row['asin']):
            lookup.setdefault(row['asin'], row)
    rows = [lookup[asin] for asin in requested if asin in lookup]
    result, public = normalize_plugin(query, {'code': 'OK', 'data': {'items': rows, 'total': None}})
    missing = [{'asin': asin, 'reason': 'not_returned_by_sellersprite'} for asin in requested if asin not in lookup]
    result['incomplete'].extend(missing)
    result.update({'source': 'sellersprite_extension_asin_api', 'candidate_source': 'amazon_anonymous_search_http',
                   'scope': 'amazon_returned_search_asins', 'match_type': 'amazon_search_results',
                   'returned_candidates': len(requested), 'lookup_returned_rows': len(rows),
                   'returned_rows_complete': not result['incomplete'], 'total_matches': None,
                   'amazon_pages_requested': cohort.get('pages_requested'),
                   'amazon_pages_loaded': len(cohort.get('pages', [])),
                   'amazon_queried_at': cohort.get('queried_at'),
                   'note': '在匿名Amazon美国站本次搜索页ASIN内按父体近30天销量取前30；不是卖家精灵关键词库结果，也未验证全量全局排名。'})
    result.pop('observed_server_units_desc', None)
    return result, public


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'plan', 'search', 'normalize'])
    parser.add_argument('--query')
    parser.add_argument('--session-har', type=Path)
    parser.add_argument('--session-file', type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--asins-file', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    if args.session_file and args.session_har:
        parser.error('Choose one local session source')
    path = args.session_file or args.session_har or os.environ.get('SELLERSPRITE_SESSION_HAR')
    loader = load_session_file if args.session_file else load_session
    try:
        if args.command == 'status':
            if not path:
                print(json.dumps({'configured': False, 'network_tested': False, 'reason': 'MISSING_SESSION_HAR'}))
            else:
                session = loader(path)
                print(json.dumps({'configured': True, 'network_tested': False,
                                  'extension_version': session['extension_version'], 'captured_at': session['captured_at']}))
            return 0
        if not args.out:
            raise ValueError('Missing output directory')
        query = validate_query(args.query)
        cohort = load_search_cohort(args.asins_file, query) if args.asins_file else None
        if args.command == 'normalize':
            if not args.input or args.input.resolve() == (args.out / 'sellersprite-candidates.json').resolve():
                raise ValueError('Separate browser input is required')
            browser = json.loads(args.input.read_text(encoding='utf-8'))
            if browser.get('marketplace') != 'US' or browser.get('keyword') != query or not isinstance(browser.get('items'), list):
                raise ApiError('BROWSER_DATA_SCOPE_MISMATCH')
            if not browser['items'] and not browser.get('explicit_empty'):
                raise ApiError('NO_CONFIRMED_PRODUCTS')
            result, public = normalize_plugin(query, {'code': 'OK', 'data': {'items': browser['items'], 'total': None}})
            result.update({'source': 'sellersprite_extension_browser', 'collection_method': 'persistent_browser_ui',
                           'total_matches': 0 if browser.get('explicit_empty') else None,
                           'sort': 'parent_units_30d_desc_then_collection_order',
                           'sales_precision': 'plugin_display',
                           'note': '从插件可见父体近30天销量列读取并在本次候选中排序；紧凑数字受显示精度限制。'})
            result.pop('observed_server_units_desc', None)
            for item in result['products']:
                item['collection_order'] = item.pop('api_order')
            public.pop('code', None)
            public.update({'source': result['source'], 'business_response_observed': False})
            write_json(args.out / 'sellersprite-candidates.json', public)
            write_json(args.out / 'ranked-products.json', result)
            summary = {'status': 'empty' if browser.get('explicit_empty') and not browser['items']
                       else 'ok' if result['returned_rows_complete'] else 'partial',
                       'products': len(result['products']), 'incomplete': len(result['incomplete']),
                       'returned_candidates': result['returned_candidates'], 'marketplace': 'US',
                       'source': result['source'], 'collection_method': 'persistent_browser_ui'}
            write_json(args.out / 'api-status.json', summary)
            print(json.dumps(summary))
            return 0
        if path and Path(path).expanduser().resolve() in [
                (args.out / name).resolve() for name in ['request-plan.json', 'ranked-products.json',
                                                       'sellersprite-candidates.json', 'api-status.json']]:
            raise ValueError('Output must not overwrite session HAR')
        write_json(args.out / 'request-plan.json', {'endpoint': ENDPOINT, 'method': 'GET',
                   'parameters': {'asins': cohort['asins']} if cohort else {'qt': 'K', 'q': query}, 'marketplace': 'US',
                   'source': 'sellersprite_extension_asin_api' if cohort else 'sellersprite_extension_keyword_api',
                   'ranking_scope': 'returned_candidates_only'})
        if args.command == 'plan':
            print(json.dumps({'status': 'planned', 'marketplace': 'US', 'requested_top': 30}))
            return 0
        session = loader(path)
        document, elapsed = request_plugin(query, session, asins=cohort['asins']) if cohort else request_plugin(query, session)
        result, public = normalize_cohort(query, document, cohort) if cohort else normalize_plugin(query, document)
        write_json(args.out / 'sellersprite-candidates.json', public)
        write_json(args.out / 'ranked-products.json', result)
        summary = {'status': 'empty' if not result['returned_candidates'] else 'ok' if result['returned_rows_complete'] else 'partial',
                   'source': result['source'], 'marketplace': 'US', 'elapsed_ms': elapsed,
                   'total_matches': result['total_matches'], 'returned_candidates': result['returned_candidates'],
                   'products': len(result['products']), 'incomplete': len(result['incomplete']),
                   'ranking_scope': result['ranking_scope'], 'global_top_30_verified': False}
        write_json(args.out / 'api-status.json', summary)
        print(json.dumps(summary))
        return 0 if result['returned_rows_complete'] else 3
    except (ApiError, OSError, ValueError) as error:
        summary = {'status': 'failed', 'category': str(error) if isinstance(error, ApiError) else 'CHECK_INPUT_OR_OUTPUT'}
        if args.out and not (path and (args.out / 'api-status.json').resolve() == Path(path).expanduser().resolve()):
            try:
                write_json(args.out / 'api-status.json', summary)
            except OSError:
                pass
        print(json.dumps(summary), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
