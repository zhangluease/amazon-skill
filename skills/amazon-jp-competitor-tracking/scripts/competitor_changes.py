"""Listing change semantics and direct title/highlight history collection."""
import datetime as dt
import json
from pathlib import Path

from competitor_core import (AUTH_ERRORS, STOP_ERRORS, ORIGIN, JST, CHANGE_KEYS, CHANGE_SCHEMA,
                             ApiError, scalar, product_row, write_json, validate_output_directory)
from competitor_report import save_listing_changes

def highlight_text(value):
    """Match extension 5.0.7 eE.asText, including array line breaks and nulls."""
    if value is None:
        return ''
    if isinstance(value, list):
        if any(isinstance(item, (list, dict)) for item in value):
            raise ApiError('INVALID_HIGHLIGHT_VALUE')
        return '\n'.join(highlight_text(item) for item in value)
    if type(value) is bool:
        return 'true' if value else 'false'
    if scalar(value) is None:
        raise ApiError('INVALID_HIGHLIGHT_VALUE')
    return str(value)


def change_evidence(data):
    """Keep only public listing fields, preserving arrays for later reprocessing."""
    if not isinstance(data, list):
        raise ApiError('INVALID_CHANGE_RESPONSE')
    return [{key: row[key] for key in CHANGE_KEYS if key in row} if isinstance(row, dict) else None
            for row in data]


def normalize_changes(data, scope):
    if not isinstance(data, list):
        raise ApiError('INVALID_CHANGE_RESPONSE')
    events, invalid_time, invalid_type, invalid_highlight = [], False, False, False
    for row in data:
        if not isinstance(row, dict):
            invalid_time = True
            continue
        point = row.get('timePoint')
        try:
            day = dt.date.fromisoformat(str(point)[:10]).isoformat()
        except ValueError:
            invalid_time = True
            continue
        if not scope['start'][:10] <= day <= scope['end'][:10]:
            continue
        kind = row.get('changeType')
        # Extension 5.0.7 enum, NOT a bit mask: 3 means bullet points, not title+image.
        valid_kind = type(kind) is int and kind in range(8)
        if not valid_kind:
            invalid_type = True
        event = {k: scalar(row[k]) for k in CHANGE_KEYS if k in row and k not in
                 ('previousImages', 'currentImages', 'previousFeatures', 'currentFeatures',
                  'previousHighlight', 'currentHighlight')}
        for key in ('previousImages', 'currentImages', 'previousFeatures', 'currentFeatures'):
            if isinstance(row.get(key), list):
                event[key] = [scalar(value) for value in row[key]]
        event['date'] = day
        event['image_changed'] = kind in (2, 4, 6, 7) if valid_kind else None
        event['title_changed'] = kind in (1, 4, 5, 7) if valid_kind else None
        event['features_changed'] = kind in (3, 5, 6, 7) if valid_kind else None
        # Highlight is separate from the features enum. The UI compares these texts.
        try:
            if not any(key in row for key in ('previousHighlight', 'currentHighlight')):
                raise ApiError('HIGHLIGHT_FIELDS_NOT_RETURNED')
            for key in ('previousHighlight', 'currentHighlight'):
                event[key] = highlight_text(row.get(key))
            event['highlight_changed'] = event['previousHighlight'] != event['currentHighlight']
        except ApiError:
            event['highlight_changed'] = None
            invalid_highlight = True
        events.append(event)
    def observed(key, invalid):
        return True if any(e[key] is True for e in events) else None if invalid_time or invalid else False
    return {'schema': CHANGE_SCHEMA,
            'status': 'partial' if invalid_time or invalid_type or invalid_highlight else 'ok',
            'title_change_observed': observed('title_changed', invalid_type),
            'highlight_change_observed': observed('highlight_changed', invalid_highlight),
            'image_change_observed': observed('image_changed', invalid_type),
            'video_change_observed': None, 'events': events, 'provider_coverage': 'not_reported',
            'provider_timestamp_timezone': 'unspecified',
            'meaning': 'provider_listing_markers; title_enum_and_highlight_text_comparison'}


def change_times(changes, name):
    values = sorted({event['timePoint'] for event in changes['events'] if event.get(name + '_changed') is True})
    return values if values or changes.get(name + '_change_observed') is False else None


def unavailable_changes():
    return {'schema': CHANGE_SCHEMA, 'status': 'unavailable', 'title_change_observed': None,
            'highlight_change_observed': None, 'image_change_observed': None,
            'video_change_observed': None, 'events': [], 'provider_coverage': 'not_reported'}


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
                    raw = saved['data']
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
        row = {'asin': asin, 'title_change_times': change_times(history, 'title'),
               'highlight_change_times': change_times(history, 'highlight'),
               'title_change_observed': history['title_change_observed'],
               'highlight_change_observed': history['highlight_change_observed'],
               'history_status': history['status'], 'source_event_count': len(history['events']),
               'errors': errors}
        results.append(row)
        save_listing_changes(output, results, asins, scope, stopped)
    usable = sum(row['title_change_observed'] is not None or row['highlight_change_observed'] is not None
                 for row in results)
    return {'status': 'authorization_required' if stopped in AUTH_ERRORS else
            'ok' if all(row['history_status'] == 'ok' for row in results) else 'partial' if usable else 'failed',
            'category': stopped, 'requested': len(asins), 'usable_products': usable,
            'title_changes': sum(len(row['title_change_times'] or []) for row in results),
            'highlight_changes': sum(len(row['highlight_change_times'] or []) for row in results),
            'output': str(output.resolve()), 'collection_method': 'independent_http'}
