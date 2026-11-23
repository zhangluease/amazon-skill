#!/usr/bin/env python3
"""CLI and orchestration for Amazon JP competitor collection; Python 3.9+ stdlib only."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import re
import shutil
import sys

from competitor_core import (AUTH_ERRORS, STOP_ERRORS, ORIGIN, JST, ASIN_RE, OFFER_FIELDS,
                             BROWSER_OFFER_SOURCE, CHANGE_SCHEMA, ApiError, Client, load_session,
                             read_inputs, parse_asin, window, history_window, checksum, scalar,
                             number, purchase_threshold, product_row, safe_image, write_json,
                             validate_output_directory)
from competitor_changes import (normalize_changes, change_times, unavailable_changes,
                                collect_listing_changes)
from competitor_report import COLUMNS, save_report

def normalize(asin, sources, changes, errors, collected_at):
    info, sales = sources.get('product', {}), sources.get('sales', {})
    amazon = sources.get('amazon', {})
    amazon_confirmed = (amazon.get('status') == 'ok' and amazon.get('delivery_destination', {}).get('verified') is True
                        and amazon.get('data', {}).get('asin') == asin)
    if not info and not sales and not amazon_confirmed and changes['status'] == 'ok' and not changes['events']:
        changes = dict(changes, status='unconfirmed_product', image_change_observed=None,
                       title_change_observed=None, highlight_change_observed=None,
                       reason='PRODUCT_IDENTITY_UNCONFIRMED')
    result = {'asin': asin, 'url': 'https://www.amazon.co.jp/dp/' + asin, 'collected_at': collected_at,
              'field_sources': {}, 'errors': errors, 'data_freshness': 'provider_snapshot; not_live_amazon_page'}
    def get(name, key, convert=scalar):
        for origin, row in (('product', info), ('sales', sales)):
            value = convert(row.get(key))
            if value is not None and value != '':
                result['field_sources'][name] = origin + '.' + key
                return value
        return None
    for name, key in [('title', 'title'), ('brand', 'brand'), ('parent_asin', 'parent'),
                      ('currency', 'currency'), ('root_category', 'bsr_label')]:
        result[name] = get(name, key)
    for name, key in [('price', 'price'), ('rating', 'rating'), ('review_count', 'reviews'), ('root_rank', 'bsr')]:
        result[name] = get(name, key, number)
    result['currency'] = result['currency'] or ('JPY' if get('symbol', 'symbol') in ('¥', '￥') else None)
    if result['currency'] in ('円', '¥', '￥'):
        result['currency'] = 'JPY'
    result['main_image'] = get('main_image', 'image_zoom', safe_image) or get('main_image', 'image', safe_image)
    ranks = info.get('bsr_list') if info.get('bsr_list') is not None else sales.get('bsr_list')
    flagged = isinstance(ranks, list) and (not ranks or any(type(r.get('main')) is bool for r in ranks))
    result['sub_ranks'] = [r for r in ranks if r.get('main') is False] if flagged else None
    if result['sub_ranks'] is not None:
        result['field_sources']['sub_ranks'] = ('product' if info.get('bsr_list') is not None else 'sales') + '.bsr_list[main=false]'
    result['parent_sales_30d'] = number(sales.get('units'))
    result['child_monthly_purchased_raw'] = scalar(info.get('month_units')) if info.get('month_units') is not None else scalar(sales.get('month_units'))
    result['child_monthly_purchased'] = purchase_threshold(result['child_monthly_purchased_raw'])
    if result['parent_sales_30d'] is not None:
        result['field_sources']['parent_sales_30d'] = 'sales.units (SellerSprite estimate)'
    if result['child_monthly_purchased'] is not None:
        result['field_sources']['child_monthly_purchased'] = ('product' if info.get('month_units') is not None else 'sales') + '.month_units (purchase threshold; extension appends +)'
    result.update(reference_price=None, discount_percent=None, buy_box=None, stock=None)
    if info.get('unavailable') is True:
        result['provider_unavailable_flag'] = True
    destination = amazon.get('delivery_destination', {'country_code': 'JP', 'verified': False})
    result['delivery_destination'] = destination
    result['delivery_status'] = '日本（已验证）' if destination.get('verified') else '日本（未验证）'
    if amazon.get('status') == 'ok' and destination.get('verified') is True:
        apply_offer(result, amazon, 'amazon_jp_verified_delivery')
    result['availability_note'] = 'Current offer fields require verified Japan delivery and explicit offer evidence; cached unavailable is not current inventory'
    result['history'] = changes
    result['image_change_observed'] = changes['image_change_observed']
    result['video_change_observed'] = changes['video_change_observed']
    result['change_dates'] = sorted({e['date'] for e in changes['events'] if e['image_changed']})
    for name in ('title', 'highlight'):
        result[name + '_change_observed'] = changes.get(name + '_change_observed')
        result[name + '_change_times'] = change_times(changes, name)
        result['field_sources'][name + '_change_times'] = 'listing.asin-change.' + name + '_changed'
    result['missing_fields'] = [key for key, _ in COLUMNS if key in result and result[key] is None]
    usable = bool(info or sales or amazon_confirmed or changes['events'] or changes['status'] == 'ok')
    result['status'] = ('ok' if usable and not result['missing_fields'] and not errors and changes['status'] == 'ok'
                        else 'partial' if usable else 'failed')
    return result


def apply_offer(result, snapshot, source):
    offer = snapshot['data']
    if offer.get('asin') != result['asin']:
        raise ApiError('AMAZON_PRODUCT_NOT_CONFIRMED')
    sources = result['field_sources']
    if not result['title'] and isinstance(offer.get('title'), str):
        result['title'] = offer['title']
        sources['title'] = source + '.title'
    for key in ('price', 'reference_price', 'discount_percent'):
        value = number(offer.get(key))
        if value is not None:
            result[key] = value
            sources[key] = source + '.' + key
    if result.get('price') is not None and offer.get('currency') == 'JPY' and offer.get('price') is not None:
        result['currency'] = 'JPY'
        sources['currency'] = source + '.currency'
    if type(offer.get('buy_box')) is bool:
        result['buy_box'] = offer['buy_box']
        sources['buy_box'] = source + '.add_to_cart'
    if offer.get('stock') in ('in_stock', 'out_of_stock'):
        result['stock'] = offer['stock']
        sources['stock'] = source + '.availability'
    result['reference_price_label'] = offer.get('reference_price_label')
    result['availability_text'] = offer.get('availability_text')
    result['delivery_destination'] = snapshot['delivery_destination']
    result['delivery_status'] = '日本（已验证）'
    result['data_freshness'] = source + '_and_provider_snapshots; see_field_sources'


def browser_offer(observation):
    if not isinstance(observation, dict):
        raise ApiError('INVALID_BROWSER_OFFER')

    def observed_text(key):
        value = observation.get(key)
        return value.strip()[:500] if isinstance(value, str) else ''

    asin = parse_asin(observed_text('url'))
    url = 'https://www.amazon.co.jp/dp/' + asin
    if observed_text('url') != url or not observed_text('title'):
        raise ApiError('AMAZON_PRODUCT_NOT_CONFIRMED')
    try:
        timestamp = dt.datetime.fromisoformat(observed_text('observed_at').replace('Z', '+00:00'))
        if timestamp.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise ApiError('INVALID_BROWSER_OFFER_TIME') from None

    japan_labels = ('日本', '日本国', 'japan')
    postal = observed_text('postal_code')
    matching_postal = (re.fullmatch(r'[0-9]{3}-[0-9]{4}', postal) is not None
                       and postal == observed_text('offer_postal_code'))
    explicit_country = observed_text('destination_country_label').casefold() in japan_labels
    if not (explicit_country or matching_postal and observed_text('country_label').casefold() in japan_labels):
        raise ApiError('AMAZON_DESTINATION_NOT_CONFIRMED')

    def yen(key):
        match = re.fullmatch(r'(?:JP)?[¥￥]\s*([\d,]+(?:\.\d+)?)', observed_text(key))
        return number(match[1]) if match else None

    price, reference = yen('price_text'), yen('reference_price_text')
    discount = re.fullmatch(r'[-−]?\s*(\d+(?:\.\d+)?)\s*%', observed_text('discount_text'))
    percent = float(discount[1]) if discount and float(discount[1]) <= 100 else None
    availability = observed_text('availability_text')
    stock = None
    if re.search(r'在庫切れ|在庫なし|缺货|无库存|Currently unavailable|Out of stock', availability, re.I):
        stock = 'out_of_stock'
    elif re.search(r'在庫あり|残り\s*\d+\s*点|现在有货|有库存|In Stock', availability, re.I):
        stock = 'in_stock'
    cart = observation.get('cart')
    cart_label = cart.get('label') if isinstance(cart, dict) else None
    buy_box = None
    if isinstance(cart_label, str) and re.fullmatch(r'加入购物车|カートに入れる|Add to Cart', cart_label.strip(), re.I):
        buy_box = cart.get('enabled') if type(cart.get('enabled')) is bool else None
    reference_label = re.match(r'^[^:：¥￥\d]+(?=[:：])', observed_text('reference_text'))
    evidence = {'price': observed_text('price_text'), 'reference_price': observed_text('reference_text'),
                'discount_percent': observed_text('discount_text'), 'buy_box': cart_label[:100] if buy_box is not None else None,
                'stock': availability}
    data = {'asin': asin, 'title': observed_text('title'), 'currency': 'JPY', 'price': price, 'reference_price': reference,
            'reference_price_label': reference_label[0].strip() if reference_label else None,
            'discount_percent': percent, 'buy_box': buy_box, 'stock': stock, 'availability_text': availability or None}
    if all(data[key] is None for key in OFFER_FIELDS):
        raise ApiError('AMAZON_BROWSER_OFFER_EMPTY')
    data['field_evidence'] = {key: value for key, value in evidence.items() if value and data[key] is not None}
    return {'status': 'ok', 'endpoint': url, 'collected_at': timestamp.isoformat(), 'data': data,
            'collection_method': 'browser_page', 'delivery_destination': {'country_code': 'JP', 'verified': True,
                'resolved_postal_code': postal if matching_postal else None,
                'selection_method': 'browser_existing_delivery'}}


def merge_browser_offers(source, output, observations):
    source, output = Path(source), Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ApiError('OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY')
    previous = json.loads((source / 'products.json').read_text(encoding='utf-8'))
    if previous.get('format') != 'amazon-jp-competitor-tracking-v1' or previous.get('marketplace') != 'JP':
        raise ApiError('INVALID_COMPETITOR_REPORT')
    rows = previous['products']
    by_asin = {row['asin']: row for row in rows}
    if any(not ASIN_RE.fullmatch(asin) for asin in by_asin):
        raise ApiError('INVALID_COMPETITOR_REPORT')
    if not isinstance(observations, list) or not observations or len(observations) > len(rows):
        raise ApiError('INVALID_BROWSER_OFFER_LIST')
    snapshots = {}
    for observation in observations:
        snapshot = browser_offer(observation)
        asin = snapshot['data']['asin']
        if asin not in by_asin or asin in snapshots:
            raise ApiError('BROWSER_OFFER_ASIN_MISMATCH_OR_DUPLICATE')
        snapshots[asin] = snapshot
    for asin, snapshot in snapshots.items():
        row = by_asin[asin]
        # Each native Amazon quote uses a single delivery observation.
        for key in OFFER_FIELDS:
            if str(row['field_sources'].get(key, '')).startswith('amazon_jp_'):
                row[key] = None
                row['field_sources'].pop(key)
        apply_offer(row, snapshot, BROWSER_OFFER_SOURCE)
        row['offer_observed_at'] = snapshot['collected_at']
        row['errors'] = [error for error in row['errors'] if error.get('source') != 'amazon']
        row['missing_fields'] = [key for key, _ in COLUMNS if key in row and row[key] is None]
        row['status'] = ('ok' if not row['missing_fields'] and not row['errors']
                         and row['history']['status'] == 'ok' else 'partial')

    output.mkdir(parents=True, exist_ok=True)
    evidence = output / 'evidence'
    evidence.mkdir()
    old_evidence = source / 'evidence'
    for asin in by_asin:
        for suffix in ('product', 'sales', 'changes', 'amazon', 'amazon-browser'):
            name = f'{asin}-{suffix}.json'
            if (old_evidence / name).is_file():
                shutil.copyfile(old_evidence / name, evidence / name)
    for asin, snapshot in snapshots.items():
        write_json(evidence / f'{asin}-amazon-browser.json', snapshot)
    save_report(output, rows, previous['requested_asins'], previous['window'], previous.get('stop_reason'))
    summary = {'status': 'ok' if all(row['status'] == 'ok' for row in rows) else 'partial',
               'requested': len(previous['requested_asins']), 'products': len(rows),
               'browser_offers_merged': len(snapshots), 'collection_method': 'http_with_browser_offers',
               'japan_delivery_verified': sum(row['delivery_destination'].get('verified') is True for row in rows),
               'output': str(output.resolve())}
    write_json(output / 'status.json', summary)
    return summary


def collect(asins, client, scope, output, *, now=None, resume=False, amazon=None):
    output = Path(output)
    validate_output_directory(output, 'products.json', resume=resume)
    if resume and (output / 'products.json').is_file():
        previous = json.loads((output / 'products.json').read_text(encoding='utf-8'))
        if (previous.get('format') != 'amazon-jp-competitor-tracking-v1'
                or previous.get('requested_asins') != asins or previous.get('window') != scope):
            raise ApiError('RESUME_INPUT_OR_WINDOW_MISMATCH')
    elif (output / 'products.json').exists():
        raise ApiError('OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY')
    else:
        resume = False
    results = []
    stopped = None
    stamp = now or dt.datetime.now(JST).isoformat()
    for asin in asins:
        sources, errors = {}, []
        if amazon is not None and not stopped:
            cached_amazon = output / 'evidence' / f'{asin}-amazon.json'
            # Do not repeat a challenge, or discard a verified offer, during the same auth retry.
            snapshot = json.loads(cached_amazon.read_text(encoding='utf-8')) if resume and cached_amazon.is_file() else amazon.fetch(asin)
            sources['amazon'] = snapshot
            write_json(cached_amazon, snapshot)
            if snapshot.get('status') != 'ok':
                error = {'source': 'amazon', 'code': snapshot.get('category', 'AMAZON_FAILED')}
                if snapshot.get('stage'):
                    error['stage'] = snapshot['stage']
                if snapshot.get('not_attempted'):
                    error['not_attempted'] = True
                errors.append(error)
        changes = unavailable_changes()
        requests = [('product', '/v2/extension/asin/JP', {'asins': asin}),
                    ('sales', '/v2/extension/competitor-lookup/JP',
                     {'q': '', 'asins': asin, 'tk': checksum(asin, client.session['extension_version'])}),
                    ('changes', '/v2/extension/listing/JP/asin-change/' + asin,
                     {'startTime': scope['start_ms'], 'endTime': scope['end_ms']})]
        for source, route, params in requests:
            if stopped:
                errors.append({'source': source, 'code': 'NOT_ATTEMPTED_AFTER_' + stopped})
                continue
            try:
                cached = output / 'evidence' / f'{asin}-{source}.json'
                if resume and cached.is_file():
                    saved = json.loads(cached.read_text(encoding='utf-8'))
                    if saved.get('endpoint') != ORIGIN + route:
                        raise ApiError('RESUME_EVIDENCE_MISMATCH')
                    if source == 'changes' and saved.get('window') != scope:
                        raise ApiError('RESUME_EVIDENCE_MISMATCH')
                    if source == 'changes':
                        changes = saved['data']
                        if changes.get('status') == 'ok' and changes.get('schema') == CHANGE_SCHEMA:
                            continue
                    if source != 'changes':
                        sources[source] = product_row([saved['data']], asin)
                        continue
                data = client.get(route, params)
                if source == 'changes':
                    changes = normalize_changes(data, scope)
                    evidence = changes
                else:
                    evidence = product_row(data, asin, sales=source == 'sales')
                    sources[source] = evidence
                write_json(output / 'evidence' / f'{asin}-{source}.json',
                           {'endpoint': ORIGIN + route, 'collected_at': dt.datetime.now(JST).isoformat(),
                            'window': scope if source == 'changes' else None, 'data': evidence})
            except ApiError as error:
                errors.append({'source': source, 'code': error.code})
                if error.code in AUTH_ERRORS or error.code in STOP_ERRORS:
                    stopped = error.code
        result = normalize(asin, sources, changes, errors, stamp)
        results.append(result)
        save_report(output, results, asins, scope, stopped)
    usable = sum(r['status'] != 'failed' for r in results)
    return {'status': 'authorization_required' if stopped in AUTH_ERRORS else
            'ok' if all(row['status'] == 'ok' for row in results) else 'partial' if usable else 'failed',
            'requested': len(asins), 'products': len(results), 'category': stopped or ('NO_PRODUCT_DATA' if not usable else None),
            'usable_products': usable,
            'browser_fallback_asins': [row['asin'] for row in results if any(row.get(key) is None for key in OFFER_FIELDS)],
            'japan_delivery_verified': sum(bool(r['delivery_destination'].get('verified')) for r in results),
            'output': str(output.resolve()), 'collection_method': 'independent_http'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('validate-input')
    run = commands.add_parser('collect')
    changes = commands.add_parser('changes', help='Only title/highlight timestamps; direct HTTP')
    merge = commands.add_parser('merge-browser-offers')
    merge.add_argument('--from', dest='source', required=True)
    merge.add_argument('--out', required=True)
    merge.add_argument('--observations', required=True, help='JSON array of browser observations; use - for stdin')
    for command in (check, run, changes):
        command.add_argument('--input')
        command.add_argument('--as-of')
        command.add_argument('asins', nargs='*')
    for command in (check, changes):
        command.add_argument('--all-history', action='store_true')
    for command in (run, changes):
        command.add_argument('--session-file', required=True)
        command.add_argument('--out', required=True)
        command.add_argument('--interval', type=float, default=1)
        command.add_argument('--resume', action='store_true', help='Reuse successful evidence after reauthorization')
    args = parser.parse_args(argv)
    try:
        if args.command == 'merge-browser-offers':
            if args.observations == '-':
                observations = json.load(sys.stdin)
            else:
                observations = json.loads(Path(args.observations).read_text(encoding='utf-8-sig'))
            result = merge_browser_offers(args.source, args.out, observations)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        asins = read_inputs(args.asins, args.input)
        scope = history_window(args.as_of, getattr(args, 'all_history', False))
        if args.command == 'validate-input':
            result = {'status': 'valid', 'asins': asins, 'window': scope}
        else:
            if not math.isfinite(args.interval) or not 1 <= args.interval <= 60:
                raise ApiError('INTERVAL_MUST_BE_1_TO_60')
            session = load_session(args.session_file)
            if args.command == 'changes':
                result = collect_listing_changes(asins, Client(session, args.interval), scope, Path(args.out),
                                                 resume=args.resume)
            else:
                from amazon_product import AmazonProductClient
                result = collect(asins, Client(session, args.interval), scope, Path(args.out), resume=args.resume,
                                 amazon=AmazonProductClient(interval=args.interval))
        print(json.dumps(result, ensure_ascii=False))
        return 3 if result['status'] == 'authorization_required' else 0
    except ApiError as error:
        print(json.dumps({'status': 'authorization_required' if error.code in AUTH_ERRORS else 'failed',
                          'category': error.code}), file=sys.stderr)
        return 3 if error.code in AUTH_ERRORS else 2
    except (OSError, ValueError, TypeError):
        print(json.dumps({'status': 'failed', 'category': 'LOCAL_INPUT_OR_OUTPUT_INVALID'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
