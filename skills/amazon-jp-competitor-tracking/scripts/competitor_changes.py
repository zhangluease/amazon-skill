"""SellerSprite Keepa combined image/video marker; direct HTTP, no chart rendering."""
import datetime as dt
import json
from pathlib import Path

from competitor_core import (AUTH_ERRORS, STOP_ERRORS, ORIGIN, JST, CHANGE_SCHEMA,
                             ApiError, scalar, product_row, write_json, validate_output_directory)
from competitor_report import save_listing_changes

MEDIA_KINDS = (2, 4, 6, 7)


def change_evidence(data):
    """Discard unrelated event kinds and retain only gallery marker evidence."""
    if not isinstance(data, list):
        raise ApiError('INVALID_CHANGE_RESPONSE')
    result = []
    for row in data:
        if not isinstance(row, dict):
            result.append(None)
            continue
        kind = row.get('changeType')
        if type(kind) is int and kind in range(8) and kind not in MEDIA_KINDS:
            continue
        event = {key: scalar(row[key]) for key in ('timePoint', 'changeType') if key in row}
        for key in ('previousImages', 'currentImages'):
            if isinstance(row.get(key), list):
                event[key] = [scalar(value) for value in row[key]]
        result.append(event)
    return result


def normalize_changes(data, scope):
    events, invalid = [], False
    for row in change_evidence(data):
        if not isinstance(row, dict):
            invalid = True
            continue
        try:
            day = dt.date.fromisoformat(str(row.get('timePoint'))[:10]).isoformat()
        except ValueError:
            invalid = True
            continue
        if not scope['start'][:10] <= day <= scope['end'][:10]:
            continue
        kind = row.get('changeType')
        # Extension 5.0.7 enum, not a bit mask. These kinds drive its gallery icon.
        if type(kind) is not int or kind not in MEDIA_KINDS:
            invalid = True
            continue
        events.append(dict(row, date=day))
    return {'schema': CHANGE_SCHEMA, 'status': 'partial' if invalid else 'ok',
            'media_change_observed': True if events else None if invalid else False,
            'events': events, 'provider_coverage': 'not_reported',
            'provider_timestamp_timezone': 'unspecified',
            'meaning': 'SellerSprite Keepa combined image/video gallery marker'}


def unavailable_changes():
    return {'schema': CHANGE_SCHEMA, 'status': 'unavailable', 'media_change_observed': None,
            'events': [], 'provider_coverage': 'not_reported'}


def collect_listing_changes(asins, client, scope, output, *, resume=False):
    """Query the marker API directly; neither Amazon pages nor Keepa/MCP are needed."""
    output = Path(output)
    validate_output_directory(output, 'changes.json', resume=resume)
    previous = output / 'changes.json'
    if previous.exists():
        if not resume:
            raise ApiError('OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY')
        saved = json.loads(previous.read_text(encoding='utf-8'))
        if saved.get('requested_asins') != asins or saved.get('window') != scope:
            raise ApiError('RESUME_INPUT_OR_WINDOW_MISMATCH')
    results, stopped = [], None
    for asin in asins:
        history, errors = unavailable_changes(), []
        if stopped:
            errors.append({'source': 'changes', 'code': 'NOT_ATTEMPTED_AFTER_' + stopped})
        else:
            try:
                endpoint = ORIGIN + '/v2/extension/listing/JP/asin-change/' + asin
                cache = output / 'evidence' / f'{asin}-listing-changes.json'
                if resume and cache.is_file():
                    saved = json.loads(cache.read_text(encoding='utf-8'))
                    if saved.get('endpoint') != endpoint or saved.get('window') != scope:
                        raise ApiError('RESUME_EVIDENCE_MISMATCH')
                    raw = change_evidence(saved['data'])
                else:
                    raw = change_evidence(client.get(endpoint.removeprefix(ORIGIN),
                        {'startTime': scope['start_ms'], 'endTime': scope['end_ms']}))
                    write_json(cache, {'endpoint': endpoint, 'window': scope,
                                      'collected_at': dt.datetime.now(JST).isoformat(), 'data': raw})
                history = normalize_changes(raw, scope)
                # An empty list alone does not confirm that the ASIN exists in this source.
                if history['status'] == 'ok' and not history['events']:
                    product = product_row(client.get('/v2/extension/asin/JP', {'asins': asin}), asin)
                    write_json(output / 'evidence' / f'{asin}-identity.json',
                               {'endpoint': ORIGIN + '/v2/extension/asin/JP',
                                'collected_at': dt.datetime.now(JST).isoformat(), 'data': product})
            except ApiError as error:
                errors.append({'source': 'changes', 'code': error.code})
                if not history['events']:
                    history = dict(unavailable_changes(), status='unconfirmed_product'
                                   if error.code in ('ASIN_NOT_RETURNED', 'PRODUCT_NOT_CACHED') else 'unavailable')
                if error.code in AUTH_ERRORS or error.code in STOP_ERRORS:
                    stopped = error.code
        row = {'asin': asin, 'media_change_observed': history['media_change_observed'],
               'history_status': history['status'], 'source_event_count': len(history['events']),
               'errors': errors}
        results.append(row)
        save_listing_changes(output, results, asins, scope, stopped)
    usable = sum(row['media_change_observed'] is not None for row in results)
    return {'status': 'verification_required' if stopped == 'ERR_ROBOT_CHECK' else
            'authorization_required' if stopped in AUTH_ERRORS else
            'ok' if all(row['history_status'] == 'ok' for row in results) else 'partial' if usable else 'failed',
            'category': stopped, 'requested': len(asins), 'usable_products': usable,
            'media_changed_products': sum(row['media_change_observed'] is True for row in results),
            'output': str(output.resolve()), 'collection_method': 'independent_http'}
