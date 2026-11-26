"""Plan once, atomically claim ASIN work, collect one item and immediately write via CLI."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

from competitor_core import ApiError, parse_asin, window, write_json, package_quantity_candidate, apply_package_image_review
from competitor_coordination import WorkQueue, ProviderGate, FileMutex, MAX_ACTIVE_ASINS, PROVIDER_PAUSES
from competitor_report import save_quantity_review
from feishu_cli import FeishuClient, apply_one, build_patch
from feishu_plan import create_plan, cell_text
from feishu_schedule import FREQUENCY_COLUMN, beijing_today, build_schedule, view_schedule_key, week_start

PAUSED = {'authorization_required', *(state[0] for state in PROVIDER_PAUSES.values())}
BUSY = {'PROFILE_BUSY', 'PROVIDER_BUSY'}
# A managed collection is bounded to 1100s; queued batch members wait for that owner.
RESOURCE_WAIT_SECONDS = MAX_ACTIVE_ASINS * 1100


def schedule_statistics(report, day):
    today = report['daily'][day.weekday()]
    return {'schedule_date': day.isoformat(), 'scheduled_groups': len(today['groups']),
            'scheduled_batches': len(today['batches']), 'scheduled_records': today['records'],
            'scheduled_asins': today['unique_asins'], 'weekly_asin_load': [entry['unique_asins'] for entry in report['daily']],
            'invalid_rows': len(report['invalid_rows']), 'invalid_frequency_groups': len(report['invalid_frequency_groups'])}


def schedule_records(queue, view_key, day, output, records, initial_plan, *, preview):
    monday = week_start(day).isoformat()
    path = queue.root / 'feishu-schedules' / view_key / f'{monday}.json'
    with FileMutex(queue.root, f'feishu-schedule:{view_key}:{monday}'):
        try:
            previous = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
            if not isinstance(previous, dict) or any(not isinstance(group, dict) for group in previous.values()):
                raise ValueError()
            due, report = build_schedule(records, initial_plan, day, previous)
        except (ValueError, TypeError) as error:
            raise ApiError('INVALID_FEISHU_WEEKLY_SCHEDULE') from error
        if not preview:
            write_json(path, {**previous, **report['batches']})
    write_json(output / 'schedule.json', report)
    return due, schedule_statistics(report, day)


def plan(client, queue, url, output, *, asins=None, as_of=None, group=None, group_prefix=None,
         scheduled=False, preview=False, schedule_day=None, resolved_target=None):
    """Read the table once; workers share the returned job ID instead of replanning."""
    if scheduled and (asins or as_of or group is not None or group_prefix is not None):
        raise ApiError('FEISHU_SCHEDULE_REQUIRES_FULL_VIEW')
    output = Path(output).resolve()
    day = (schedule_day or beijing_today()) if scheduled else None
    target = resolved_target or client.resolve(url)
    schedule = (view_schedule_key(target), day.isoformat()) if day else None
    if schedule and not preview and (existing := queue.scheduled_job(schedule)):
        canonical_output = queue.job(existing)['output']
        result = {'status': 'planned', 'job_id': existing, 'schedule_date': day.isoformat(),
                  'reused_scheduled_job': True, 'output': canonical_output}
        original_schedule = Path(canonical_output) / 'schedule.json'
        if original_schedule.is_file():
            result.update(schedule_statistics(json.loads(original_schedule.read_text(encoding='utf-8')), day))
        write_json(output / 'job.json', result)
        return result
    schema = client.fields(target)
    if scheduled and not (any(field['name'] == '分组' for field in schema)
                          and any(field['name'] == FREQUENCY_COLUMN and field['type'] == 'select' for field in schema)):
        raise ApiError('FEISHU_SCHEDULE_COLUMNS_REQUIRED')
    planning_columns = {'分组', 'ASIN', '标题', '链接', FREQUENCY_COLUMN}
    records = client.records(target, [field for field in schema if field['name'] in planning_columns] if preview else schema)
    if group is not None or group_prefix is not None:
        if not any(field['name'] == '分组' for field in schema):
            raise ApiError('FEISHU_GROUP_COLUMN_MISSING')
        selected = group if group is not None else group_prefix
        if not selected.strip():
            raise ApiError('EMPTY_FEISHU_GROUP')
        groups = {cell_text(row.get('分组')) for row in records}
        if group is None:
            matches = [name for name in groups if name.casefold().startswith(selected.strip().casefold())]
            if len(matches) > 1:
                raise ApiError('FEISHU_GROUP_PREFIX_AMBIGUOUS')
            group = matches[0] if matches else None
        else:
            group = group.strip()
        if group not in groups:
            raise ApiError('FEISHU_GROUP_NOT_FOUND')
        records = [row for row in records if cell_text(row.get('分组')) == group]
    if asins:
        requested = set(asins)
        records = [row for row in records if cell_text(row.get('ASIN')) in requested]
        if {cell_text(row.get('ASIN')) for row in records} != requested:
            raise ApiError('REQUESTED_ASIN_NOT_IN_FEISHU_VIEW')
    columns = [field['name'] for field in schema]
    planned = create_plan(records, url, columns=columns)
    schedule_summary = {}
    if scheduled:
        records, schedule_summary = schedule_records(queue, schedule[0], day, output, records, planned, preview=preview)
        if preview or not records:
            return {'status': 'preview' if preview else 'nothing_due', 'output': str(output), **schedule_summary}
        planned = create_plan(records, url, columns=columns)
    elif planned['invalid_rows']:
        raise ApiError('INVALID_FEISHU_PLAN_ROWS')
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'fields.json', schema)
    write_json(output / 'records.json', records)
    write_json(output / 'plan.json', planned)
    job_id = queue.create(target, window(as_of), output, records, planned,
                          join_active_window=as_of is None, schedule=schedule)
    canonical_output = queue.job(job_id)['output']
    write_json(output / 'job.json', {'job_id': job_id, 'coordination_directory': str(queue.root),
                                   'target': target, 'new_rows': len(planned['new_rows']),
                                   'update_rows': len(planned['update_rows'])})
    return {'status': 'planned', 'job_id': job_id, 'new_rows': len(planned['new_rows']),
            'update_rows': len(planned['update_rows']), 'output': canonical_output,
            **({'group': group} if group is not None else {}),
            **schedule_summary,
            'reused_active_job': canonical_output != str(output)}


def collect_one(asin, output, scope, coordinator_root, *, unattended=False):
    node = shutil.which('node')
    if not node:
        raise ApiError('NODE_18_REQUIRED')
    args = [node, str(Path(__file__).with_name('competitor.cjs')), 'collect', asin,
            '--out', str(output), '--as-of', scope['end']]
    if unattended:
        args.append('--no-browser-login')
    env = dict(os.environ, AMAZON_COMPETITOR_COORDINATION_DIR=str(coordinator_root))
    try:
        # Child stderr carries the public login handoff and must be visible while the window is open.
        result = subprocess.run(args, stdout=subprocess.PIPE, text=True, encoding='utf-8', env=env, timeout=1100)
        summary = result.stdout if result.stdout.strip() else (Path(output) / 'status.json').read_text(encoding='utf-8')
        public = json.loads(summary)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise ApiError('COLLECTOR_PROCESS_FAILED') from None
    if not isinstance(public, dict):
        raise ApiError('INVALID_COLLECTOR_SUMMARY')
    return public


def load_product(path, asin, scope, output_root, expected_hash=None):
    path = Path(path).resolve()
    if Path(output_root).resolve() not in path.parents:
        raise ApiError('SNAPSHOT_OUTSIDE_JOB')
    data = path.read_bytes()
    if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
        raise ApiError('SNAPSHOT_CHECKSUM_MISMATCH')
    value = json.loads(data.decode('utf-8-sig'))
    products = value.get('products') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get('format') != 'amazon-jp-competitor-tracking-v1'
            or value.get('requested_asins') != [asin] or value.get('window') != scope
            or not isinstance(products, list) or len(products) != 1
            or not isinstance(products[0], dict) or products[0].get('asin') != asin):
        raise ApiError('TASK_PRODUCT_OR_WINDOW_MISMATCH')
    return value['products'][0]


def preflight(client, job, schema, claim):
    """Reject stale agent-level reads before spending requests on the wrong work."""
    checkpoint = claim.row['checkpoint']
    current = client.records(job['target'], schema, [row['record_id'] for row in claim.row['records']])
    by_id = {row['record_id']: row for row in current}
    if set(by_id) != {row['record_id'] for row in claim.row['records']}:
        raise ApiError('FEISHU_RECORD_NOT_FOUND')
    bases = checkpoint.setdefault('bases', {})
    for original in claim.row['records']:
        record_id = original['record_id']
        expected = bases.get(record_id, original)
        actual = by_id[record_id]
        if cell_text(actual.get('ASIN')) != claim.row['asin']:
            raise ApiError('FEISHU_PRODUCT_IDENTITY_MISMATCH')
        operation = checkpoint.get('operations', {}).get(record_id)
        # A previous process may have died after the server wrote but before acknowledgement.
        if operation and all(actual.get(name) == value for name, value in operation['fields'].items()):
            watched = set(expected) - set(operation['fields'])
            system_fields = {field['name'] for field in schema if field.get('type') == 'updated_at'}
            if any(actual.get(name) != expected.get(name) for name in watched - system_fields):
                raise ApiError('FEISHU_RECORD_CHANGED_SINCE_READ')
            bases[record_id] = actual
        elif actual != expected:
            raise ApiError('FEISHU_RECORD_CHANGED_SINCE_READ')
    claim.checkpoint(checkpoint)
    return bases


def review_package_image(product, attempt, job, emit, wait_seconds):
    candidate = package_quantity_candidate(product)
    if not candidate or not wait_seconds:
        return False
    reply = attempt / 'package-image-review.json'
    emit({'event': 'package_quantity_review', **candidate, 'job_id': job['id'], 'reply_file': str(reply.resolve())})
    deadline = time.monotonic() + wait_seconds
    while not reply.is_file():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            emit({'event': 'package_quantity_review_timeout', 'asin': product['asin']})
            return False
        time.sleep(min(0.25, remaining))
    try:
        review = json.loads(reply.read_text(encoding='utf-8'))
        return apply_package_image_review(product, review)
    except (OSError, ValueError, ApiError) as error:
        emit({'event': 'package_quantity_review_invalid', 'asin': product['asin'],
              'category': error.code if isinstance(error, ApiError) else 'INVALID_PACKAGE_IMAGE_REVIEW'})
        return False


def process_claim(queue, job, schema, claim, client, collector, emit, quantity_review_seconds):
    asin = claim.row['asin']
    bases = preflight(client, job, schema, claim)
    cached = queue.snapshot(job['id'], asin)
    status = None
    if cached:
        product = load_product(cached['path'], asin, job['window'], job['output'], cached['sha256'])
    else:
        attempt = Path(job['output']) / 'items' / asin / uuid.uuid4().hex
        # Serialize entire managed collection/login handoffs across agent workers.
        collection_lock = FileMutex(queue.root, 'sellersprite-collection', timeout=RESOURCE_WAIT_SECONDS)
        if not collection_lock.acquire():
            claim.finish('pending', 'PROVIDER_BUSY')
            return 'resource_busy'
        try:
            provider = ProviderGate(queue.root).state()
            if provider['state'] != 'ready':
                claim.finish('blocked', provider.get('reason'))
                return PROVIDER_PAUSES[provider['state']][0]
            status = collector(asin, attempt, job['window'], queue.root)
            if status.get('status') == 'failed' and status.get('category') not in BUSY:
                provider = ProviderGate(queue.root).state()
                if provider['state'] in PROVIDER_PAUSES:
                    status = dict(status, status=PROVIDER_PAUSES[provider['state']][0])
                elif status.get('authorization_reason'):
                    status = dict(status, status='authorization_required')
        finally:
            collection_lock.release()
        if status.get('category') in BUSY or status.get('status') == 'resource_busy':
            claim.finish('pending', status.get('category') or 'PROVIDER_BUSY')
            return 'resource_busy'
        path = attempt / 'products.json'
        if not path.is_file():
            if status.get('status') in PAUSED:
                claim.finish('blocked', status.get('category'))
                return status['status']
            raise ApiError(status.get('category') or 'COLLECTOR_DID_NOT_PRODUCE_PRODUCT')
        product = load_product(path, asin, job['window'], job['output'])
        if (status.get('status') not in PAUSED and any(field['name'] == '数量装' for field in schema)
                and review_package_image(product, attempt, job, emit, quantity_review_seconds)):
            report = json.loads(path.read_text(encoding='utf-8'))
            report['products'] = [product]
            save_quantity_review(attempt, report, changed=True)
        queue.save_snapshot(claim, path, status.get('status') not in PAUSED and bool(product.get('title')))
    if not product.get('title'):
        if status and status.get('status') in PAUSED:
            claim.finish('blocked', status.get('category'))
            return status['status']
        raise ApiError('PRODUCT_IDENTITY_NOT_CONFIRMED')

    # This lock covers read/guard/update/readback, not just the HTTP write call.
    key = 'feishu:' + job['target']['base_token'] + ':' + job['target']['table_id']
    with FileMutex(queue.root, key, timeout=RESOURCE_WAIT_SECONDS) as table_lock:
        client.inherited_lock_fds = (claim.lock.fd, claim.slot.fd, table_lock.fd)
        try:
            for original in claim.row['records']:
                claim.assert_owned()
                record_id = original['record_id']
                before = bases.get(record_id, original)
                patch = build_patch(product, before, schema, price_baseline=original)
                claim.row['checkpoint'].setdefault('operations', {})[record_id] = {'fields': patch['fields']}
                claim.checkpoint(claim.row['checkpoint'])
                receipt = apply_one(client, job['target'], schema, before, product,
                                    price_baseline=original, ownership_check=claim.assert_owned)
                bases[record_id] = receipt['after']
                claim.checkpoint(claim.row['checkpoint'])
                emit({'event': 'record_verified', 'job_id': job['id'], 'asin': asin,
                      'record_id': record_id, 'fields': len(receipt['fields']), 'operation': receipt['operation']})
        finally:
            client.inherited_lock_fds = ()
    if status and status.get('status') in PAUSED:
        claim.finish('blocked', status.get('category'))
        return status['status']
    claim.finish('done' if product.get('status') == 'ok' else 'partial')
    return 'written'


def work(queue, job_id, *, client_factory=FeishuClient, collector=None, max_items=None,
         concurrency=MAX_ACTIVE_ASINS, quantity_review_seconds=0, unattended=False, emit=lambda _: None):
    if type(concurrency) is not int or not 1 <= concurrency <= MAX_ACTIVE_ASINS:
        raise ApiError('CONCURRENCY_MUST_BE_1_TO_3')
    if type(quantity_review_seconds) is not int or not 0 <= quantity_review_seconds <= 300:
        raise ApiError('QUANTITY_REVIEW_SECONDS_MUST_BE_0_TO_300')
    collector = collector or partial(collect_one, unattended=unattended)
    job = queue.job(job_id)
    schema = json.loads((Path(job['output']) / 'fields.json').read_text())
    initial = queue.status(job_id)
    pending = any(row['status'] in ('pending', 'running', 'blocked') for row in initial['items'])
    if pending:
        provider = initial['provider']
        if provider['state'] != 'ready':
            return {'status': PROVIDER_PAUSES[provider['state']][0], 'job_id': job_id, 'provider': provider,
                    'processed': 0, 'concurrency': concurrency, 'queue': initial}
        queue.resume(job_id)
    # Each item has its own CLI client/output and claim. Only event output is shared.
    output_lock = threading.Lock()
    def publish(value):
        with output_lock:
            emit(value)

    def run_claim(claim):
        try:
            client = client_factory(Path(job['output']) / 'writeback' / uuid.uuid4().hex)
            return process_claim(queue, job, schema, claim, client, collector, publish, quantity_review_seconds)
        except ApiError as error:
            if error.code == 'CLAIM_LOST':
                raise
            if error.code in BUSY:
                claim.finish('pending', error.code)
                publish({'event': 'waiting_for_resource', 'asin': claim.row['asin'], 'category': error.code})
                return 'resource_busy'
            claim.finish('stale' if error.code == 'FEISHU_RECORD_CHANGED_SINCE_READ' else 'failed', error.code)
            publish({'event': 'item_failed', 'asin': claim.row['asin'], 'category': error.code})
            return 'failed'
        except (OSError, ValueError, KeyError, TypeError):
            claim.finish('failed', 'INVALID_TASK_INPUT_OR_OUTPUT')
            publish({'event': 'item_failed', 'asin': claim.row['asin'], 'category': 'INVALID_TASK_INPUT_OR_OUTPUT'})
            return 'failed'
        finally:
            claim.close()

    processed, stopped, batch = 0, None, 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        while max_items is None or processed < max_items:
            capacity = concurrency if max_items is None else min(concurrency, max_items - processed)
            claims, futures = [], []
            try:
                # Claim this batch atomically per item before starting its workers.
                for _ in range(capacity):
                    claim = queue.claim(job_id)
                    if claim is None:
                        break
                    claims.append(claim)
                if not claims:
                    break
                batch += 1
                for claim in claims:
                    publish({'event': 'claimed', 'job_id': job_id, 'asin': claim.row['asin'], 'batch': batch,
                             'phase': 'new' if claim.row['phase'] == 0 else 'update',
                             'record_ids': [row['record_id'] for row in claim.row['records']]})
                    futures.append(executor.submit(run_claim, claim))
                # Writes happen inside each worker; only the next batch waits for the batch to finish.
                results = [future.result() for future in futures]
                processed += len(results)
                stopped = next((state for state in results if state in PAUSED), None)
                if stopped is None and 'resource_busy' in results:
                    stopped = 'resource_busy'
                if stopped:
                    break
                provider = ProviderGate(queue.root).state()
                if provider['state'] != 'ready':
                    stopped = PROVIDER_PAUSES[provider['state']][0]
                    break
            finally:
                # Submitted workers own their claims; do not release a still-running worker's locks.
                for claim in claims[len(futures):]:
                    claim.close()
    state = queue.status(job_id)
    statuses = [row['status'] for row in state['items']]
    result_status = (stopped or
                     ('in_progress' if any(x in ('pending', 'running', 'blocked') for x in statuses) else
                      'complete_with_gaps' if any(x != 'done' for x in statuses) else 'complete'))
    result = {'status': result_status, 'job_id': job_id, 'processed': processed,
              'concurrency': concurrency, 'batches': batch, 'queue': state}
    write_json(Path(job['output']) / ('worker-' + uuid.uuid4().hex + '.json'), result)
    return result


def summarize_job(queue, result):
    job_id = result['job_id']
    output = Path(queue.job(job_id)['output'])
    state = result['queue']
    receipts, updated = {}, set()
    for path in sorted((output / 'writeback').glob('*/after-rec*.json'), key=lambda item: item.stat().st_mtime_ns):
        receipt = json.loads(path.read_text(encoding='utf-8'))
        record_id = receipt['record_id']
        receipts[record_id] = receipt
        if receipt['operation'] == 'updated':
            updated.add(record_id)
    fields = [receipt['fields'] for receipt in receipts.values()]
    missing = Counter(item['field'] for receipt in receipts.values() for item in receipt['skipped']
                      if item['reason'] == 'source_unknown')
    attempts = {}
    for path in sorted((output / 'items').glob('*/*/products.json'), key=lambda item: item.stat().st_mtime_ns):
        attempts[path.parent.parent.name] = path
    source_errors = Counter()
    for path in attempts.values():
        products = json.loads(path.read_text(encoding='utf-8'))['products']
        source_errors.update({error['code'] for row in products for error in row.get('errors', [])
                              if not error['code'].startswith('NOT_ATTEMPTED_AFTER_')})
    return {'status': result['status'], 'job_id': job_id, 'output': str(output),
            'valid_asins': len({row['asin'] for row in state['items']}), 'processed_items': result['processed'],
            'item_statuses': dict(Counter(row['status'] for row in state['items'])),
            'item_errors': dict(Counter(row['error'] for row in state['items'] if row['error'])),
            'verified_records': len(receipts), 'updated_and_verified_records': len(updated),
            'media_changed_records': sum(row.get('图片变化') == ['是'] for row in fields),
            'price_changed_records': sum(row.get('价格波动（超10%）') == ['是'] for row in fields),
            'unknown_fields_by_record': dict(missing), 'source_error_asin_counts': dict(source_errors),
            **({'provider': state['provider']} if state['provider']['state'] != 'ready' else {})}


def nightly(client, queue, url, output, *, concurrency=MAX_ACTIVE_ASINS, quantity_review_seconds=0,
            emit=lambda _: None, worker=work, schedule_day=None):
    """Run the due Feishu job unattended, including one earlier unfinished scheduled job."""
    day = schedule_day or beijing_today()
    output = Path(output).resolve()
    report_path = output / 'nightly-summary.json'
    result = {'schedule_date': day.isoformat(), 'report': str(report_path)}

    def finish(status, **details):
        summary = dict(result, status=status, **details)
        write_json(report_path, summary)
        return summary

    try:
        target = client.resolve(url)
        view_key = view_schedule_key(target)
        with FileMutex(queue.root, 'feishu-nightly:' + view_key, timeout=0):
            def run(job_id):
                task = worker(queue, job_id, concurrency=concurrency, unattended=True,
                              quantity_review_seconds=quantity_review_seconds,
                              emit=lambda event: emit(event) if event['event'] == 'package_quantity_review' else None)
                return summarize_job(queue, task)

            earlier = queue.earlier_scheduled_job(view_key, day.isoformat())
            if earlier:
                result['resumed'] = dict(run(earlier['job_id']), schedule_date=earlier['date'])
                if result['resumed']['status'] not in ('complete', 'complete_with_gaps'):
                    return finish(result['resumed']['status'], stage='resume_earlier_job')
            result['plan'] = plan(client, queue, url, output, scheduled=True, schedule_day=day, resolved_target=target)
            gaps = (result.get('resumed', {}).get('status') == 'complete_with_gaps'
                    or result['plan'].get('invalid_rows', 0) or result['plan'].get('invalid_frequency_groups', 0))
            if result['plan']['status'] == 'nothing_due':
                return finish('complete_with_gaps' if gaps else 'nothing_due')
            result['job'] = run(result['plan']['job_id'])
            status = result['job']['status']
            return finish('complete_with_gaps' if gaps and status == 'complete' else status)
    except ApiError as error:
        if error.code == 'PROVIDER_BUSY':
            return finish('resource_busy', category=error.code)
        return finish('failed', category=error.code, **({'active_job_id': error.active_job_id}
                      if hasattr(error, 'active_job_id') else {}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'work', 'status', 'nightly'))
    parser.add_argument('--coord-dir')
    parser.add_argument('--url')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--asin', action='append')
    group_filter = parser.add_mutually_exclusive_group()
    group_filter.add_argument('--group', help='Exact 分组 cell value')
    group_filter.add_argument('--group-prefix', help='Case-insensitive prefix matching exactly one group')
    parser.add_argument('--as-of')
    parser.add_argument('--scheduled', action='store_true', help='Select today’s due groups by Beijing time')
    parser.add_argument('--preview', action='store_true', help='Save the weekly schedule without creating a job')
    parser.add_argument('--schedule-date', type=date.fromisoformat, help='Beijing date for --scheduled --preview')
    parser.add_argument('--job-id')
    parser.add_argument('--max-items', type=int)
    parser.add_argument('--unattended', action='store_true', help='Use API renewal but defer interactive login for work')
    parser.add_argument('--concurrency', type=int, choices=range(1, MAX_ACTIVE_ASINS + 1),
                        default=MAX_ACTIVE_ASINS, help='ASIN workers per batch; same-host global cap is 3')
    parser.add_argument('--quantity-review-seconds', type=int, default=0,
                        help='Wait up to this many seconds per ASIN for optional main-image observation (0–300)')
    args = parser.parse_args()
    try:
        if ((args.scheduled and args.command != 'plan') or (args.preview and not args.scheduled)
                or (args.schedule_date and not args.preview)):
            raise ApiError('INVALID_FEISHU_SCHEDULE_OPTIONS')
        queue = WorkQueue(args.coord_dir)
        if args.command in ('plan', 'nightly'):
            if not args.url or not args.out:
                raise ApiError('PLAN_URL_AND_OUTPUT_REQUIRED')
            if args.out.exists() and any(args.out.iterdir()):
                raise ApiError('OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY')
            if args.command == 'nightly':
                if args.asin or args.as_of or args.group is not None or args.group_prefix is not None or args.job_id or args.max_items is not None:
                    raise ApiError('FEISHU_SCHEDULE_REQUIRES_FULL_VIEW')
                result = nightly(FeishuClient(args.out), queue, args.url, args.out, concurrency=args.concurrency,
                                 quantity_review_seconds=args.quantity_review_seconds,
                                 emit=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
                print(json.dumps(result, ensure_ascii=False))
                return 0 if result['status'] in ('complete', 'nothing_due') else 2
            asins = [parse_asin(value) for value in args.asin] if args.asin else None
            result = plan(FeishuClient(args.out), queue, args.url, args.out, asins=asins, as_of=args.as_of,
                          group=args.group, group_prefix=args.group_prefix, scheduled=args.scheduled,
                          preview=args.preview, schedule_day=args.schedule_date)
        else:
            if args.group is not None or args.group_prefix is not None:
                raise ApiError('FEISHU_GROUP_FILTER_REQUIRES_PLAN')
            if not args.job_id:
                if args.command != 'status':
                    raise ApiError('JOB_ID_REQUIRED')
                print(json.dumps({'active_jobs': queue.active_jobs()}, ensure_ascii=False))
                return 0
            if args.max_items is not None and args.max_items <= 0:
                raise ApiError('MAX_ITEMS_MUST_BE_POSITIVE')
            result = queue.status(args.job_id) if args.command == 'status' else work(
                queue, args.job_id, max_items=args.max_items, concurrency=args.concurrency,
                quantity_review_seconds=args.quantity_review_seconds, unattended=args.unattended,
                emit=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result.get('status') in PAUSED | {'resource_busy', 'complete_with_gaps'} else 0
    except ApiError as error:
        print(json.dumps({'status': 'failed', 'category': error.code,
                          **({'active_job_id': error.active_job_id} if hasattr(error, 'active_job_id') else {})}), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'status': 'failed', 'category': 'INVALID_TASK_INPUT_OR_OUTPUT'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
