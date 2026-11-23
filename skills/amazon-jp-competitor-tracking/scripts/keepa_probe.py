#!/usr/bin/env python3
"""Inspect the extension's Keepa and variation sources without changing the report."""
import argparse
import csv
import json
from pathlib import Path
import re
import sys

from competitor_client import (AUTH_ERRORS, ORIGIN, ApiError, Client, load_session, normalize_changes,
                               parse_asin, product_row, purchase_threshold, scalar, window, write_json)

CHART_SERIES = ('price', 'dealPrice', 'buyPrice', 'listPrice', 'primePrice', 'fbaPrice', 'fbmPrice', 'bsr',
                'rootRanks', 'subRanks', 'rating', 'reviews', 'sellers', 'monthlySoldHistory',
                'oneTimeCouponHistory', 'subscribeCouponHistory')


def probe(asin, client, scope, *, chart=True):
    start_end = {'startTime': scope['start_ms'], 'endTime': scope['end_ms']}
    jp_asin = {'station': 'JP', 'asin': asin}
    routes = [('identity', '/asin/JP', {'asins': asin}),
              ('listing_changes', f'/listing/JP/asin-change/{asin}', start_end),
              ('advertising_changes', f'/listing/JP/ads-trend/{asin}', start_end),
              ('variation_purchases', '/chart/amz-unit-trend/variations', jp_asin)]
    if chart:
        routes.append(('keepa_chart', '/keepa', dict(jp_asin, period='DAY')))
    results = {}
    points = None
    stopped = None
    for key, suffix, params in routes:
        route = '/v2/extension' + suffix
        record = {'endpoint': ORIGIN + route, 'parameters': params}
        results[key] = record
        if stopped:
            record.update(status='skipped', code=stopped)
            continue
        try:
            data = client.get(route, params)
            if key == 'identity':
                product_row(data, asin)
            elif key == 'listing_changes':
                history = normalize_changes(data, scope)
                record.update(events_count=len(history['events']), observation_status=history['status'],
                              video_marker='not_exposed')
            elif key == 'advertising_changes':
                if not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in ('adsCampaign', 'adsGroup')):
                    raise ApiError('UNRECOGNIZED_ADS_TREND_RESPONSE')
                record.update(campaign_events=len(data['adsCampaign']), ad_group_events=len(data['adsGroup']))
            elif key == 'variation_purchases':
                if not isinstance(data, list):
                    raise ApiError('UNRECOGNIZED_VARIATION_TREND_RESPONSE')
                matched = [r for r in data if isinstance(r, dict) and r.get('asin') == asin]
                if len(matched) != 1 or not isinstance(matched[0].get('trend'), list):
                    raise ApiError('VARIATION_TREND_ASIN_NOT_CONFIRMED')
                points = []
                for entry in matched[0]['trend']:
                    if not isinstance(entry, dict) or not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', str(entry.get('date'))):
                        raise ApiError('UNRECOGNIZED_VARIATION_TREND_POINT')
                    value = scalar(entry.get('unit'))
                    points.append({'month': entry['date'], 'unit_raw': value,
                                   'purchase_threshold': purchase_threshold(value) if value != 0 else '0'})
                record.update(variations_count=len(data), points_count=len(points))
            else:
                if not isinstance(data, dict) or not isinstance(data.get('times'), list) or not isinstance(data.get('keepa'), dict):
                    raise ApiError('UNRECOGNIZED_KEEPA_RESPONSE')
                series = data['keepa']
                record.update(points_count=len(data['times']), series=[k for k in CHART_SERIES if k in series],
                              first_time=scalar(data['times'][0]) if data['times'] else None,
                              last_time=scalar(data['times'][-1]) if data['times'] else None,
                              updated_at_raw=scalar(data.get('updateTime')))
            record['status'] = 'ok'
        except ApiError as error:
            record.update(status='failed', code=error.code)
            if key == 'variation_purchases':
                points = None
            if error.code in AUTH_ERRORS or error.code == 'HTTP_429':
                stopped = error.code
    return {'asin': asin, 'marketplace': 'JP', 'window': scope, 'sources': results}, points


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('asin', help='Amazon Japan ASIN or product URL')
    parser.add_argument('--session-file', type=Path, required=True)
    parser.add_argument('--as-of')
    parser.add_argument('--skip-chart', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        payload, points = probe(parse_asin(args.asin), Client(load_session(args.session_file)), window(args.as_of),
                                chart=not args.skip_chart)
        write_json(args.out / 'keepa-api-probe.json', payload)
        if points is not None:
            with (args.out / 'variation-monthly-purchases.csv').open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=('month', 'unit_raw', 'purchase_threshold'))
                writer.writeheader()
                writer.writerows(points)
        print(json.dumps({key: record.get('code', record['status']) for key, record in payload['sources'].items()}))
        return 0 if any(row['status'] == 'ok' for row in payload['sources'].values()) else 2
    except (ApiError, OSError, ValueError) as error:
        print(json.dumps({'status': 'failed', 'code': error.code if isinstance(error, ApiError) else 'LOCAL_INPUT_ERROR'}),
              file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
