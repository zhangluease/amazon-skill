#!/usr/bin/env python3
"""Extract a minimal, credential-free SellerSprite request diagnostic from a local HAR."""
import argparse
import base64
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

HOSTS = {'e.sellersprite.com', 'www.sellersprite.com'}
ROUTE = re.compile(r'/v2/extension/competitor-lookup/(?:quick-view/)?[A-Z]{2}')
PARAMS = {'asins', 'asin', 'q', 'qt', 'marketplace', 'market', 'station',
          'version', 'language', 'source', 'miniMode', 'withRelation', 'withSaleTrend'}
NUMBERS = {'units', 'amount', 'amz_unit', 'amzUnit', 'price', 'bsr', 'variations', 'revenue'}


def parameters(pairs):
    return [{'name': name, 'value': str(value)[:2000] if name in PARAMS else '[REDACTED]'}
            for name, value in pairs]


def product_summary(item):
    result = {'field_names': sorted(item)}
    for name in ['asin', 'parent']:
        value = item.get(name)
        if isinstance(value, str) and re.fullmatch(r'[A-Z0-9]{10}', value):
            result[name] = value
    for name in NUMBERS:
        value = item.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result[name] = value
    for name in ['image', 'image_zoom']:
        value = item.get(name)
        if isinstance(value, str):
            # An image's presence and host suffice for protocol analysis. Never keep signed URLs.
            try:
                result[name + '_host'] = urlsplit(value).hostname
            except ValueError:
                pass
    return result


def response_summary(content):
    text = content.get('text', '')
    if not text:
        return {'body_available': False}
    try:
        if content.get('encoding') == 'base64':
            text = base64.b64decode(text, validate=True).decode('utf-8')
        document = json.loads(text)
    except (ValueError, UnicodeError):
        return {'body_available': True, 'json_available': False}
    result = {'body_available': True, 'json_available': True}
    if not isinstance(document, dict):
        return result
    code = document.get('code')
    if isinstance(code, str) and re.fullmatch(r'[A-Z0-9_]{1,80}', code):
        result['business_code'] = code
    data = document.get('data')
    if isinstance(data, dict):
        result['data_fields'] = sorted(data)
        if isinstance(data.get('total'), (int, float)) and math.isfinite(data['total']):
            result['total'] = data['total']
        if isinstance(data.get('items'), list):
            result['item_count'] = len(data['items'])
            result['sample_items'] = [product_summary(row) for row in data['items'][:10] if isinstance(row, dict)]
    return result


def inspect_har(document):
    entries = document.get('log', {}).get('entries')
    if not isinstance(entries, list):
        raise ValueError('INVALID_HAR')
    requests = []
    for entry in entries:
        request = entry.get('request', {})
        try:
            url = urlsplit(request.get('url', ''))
        except ValueError:
            continue
        if url.scheme != 'https' or url.hostname not in HOSTS or not ROUTE.fullmatch(url.path):
            continue
        pairs = parse_qsl(url.query, keep_blank_values=True)
        query = parameters(pairs)
        clean_url = urlunsplit(('https', url.hostname, url.path,
                               urlencode([(p['name'], p['value']) for p in query]), ''))
        response = entry.get('response', {})
        record = {'method': request.get('method'), 'url': clean_url, 'query': query,
                  'request_header_names': sorted({str(h.get('name', '')).lower() for h in request.get('headers', [])}),
                  'http_status': response.get('status'),
                  'response': response_summary(response.get('content', {}))}
        post = request.get('postData', {})
        if isinstance(post.get('text'), str):
            try:
                body = json.loads(post['text'])
                if isinstance(body, dict):
                    record['request_json_fields'] = sorted(body)
                    record['request_json_parameters'] = parameters((k, v) for k, v in body.items()
                        if isinstance(v, (str, int, float, bool)))
            except ValueError:
                record['request_body_available'] = True
        requests.append(record)
    return {'format': 'sellersprite-har-diagnostic-v1', 'matching_requests': len(requests),
            'header_values_included': False, 'cookies_included': False, 'requests': requests}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('har', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.har.resolve() == args.output.resolve():
            raise ValueError('OUTPUT_MUST_DIFFER')
        if args.har.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('HAR_TOO_LARGE')
        result = inspect_har(json.loads(args.har.read_text(encoding='utf-8-sig')))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps({'matching_requests': result['matching_requests'], 'output': str(args.output)}))
        return 0
    except (OSError, ValueError, TypeError, AttributeError):
        print('{"status":"failed","reason":"Check HAR format, file size and separate output path."}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
