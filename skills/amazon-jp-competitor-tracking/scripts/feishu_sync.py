"""Plan once, atomically claim ASIN work, collect one item and immediately write via CLI."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid

from competitor_core import ApiError, parse_asin, window, write_json
from competitor_coordination import WorkQueue, ProviderGate, FileMutex, MAX_ACTIVE_ASINS
from feishu_cli import FeishuClient, apply_one, build_patch
from feishu_plan import create_plan, cell_text

PAUSED = {'authorization_required', 'verification_required', 'authorization_pending'}
BUSY = {'PROFILE_BUSY', 'PROVIDER_BUSY'}
# A managed collection is bounded to 1100s; queued batch members wait for that owner.
RESOURCE_WAIT_SECONDS = MAX_ACTIVE_ASINS * 1100


def plan(client, queue, url, output, *, asins=None, as_of=None):
    """Read the table once; workers share the returned job ID instead of replanning."""
    target = client.resolve(url)
    schema = client.fields(target)
    records = client.records(target, schema)
    if asins:
        requested = set(asins)
        records = [row for row in records if cell_text(row.get('ASIN')) in requested]
        if {cell_text(row.get('ASIN')) for row in records} != requested:
            raise ApiError('REQUESTED_ASIN_NOT_IN_FEISHU_VIEW')
    planned = create_plan(records, url, columns=[field['name'] for field in schema])
    if planned['invalid_rows']:
        raise ApiError('INVALID_FEISHU_PLAN_ROWS')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'fields.json', schema)
    write_json(output / 'records.json', records)
    write_json(output / 'plan.json', planned)
    job_id = queue.create(target, window(as_of), output, records, planned, join_active_window=as_of is None)
    canonical_output = queue.job(job_id)['output']
    write_json(output / 'job.json', {'job_id': job_id, 'coordination_directory': str(queue.root),
                                   'target': target, 'new_rows': len(planned['new_rows']),
                                   'update_rows': len(planned['update_rows'])})
    return {'status': 'planned', 'job_id': job_id, 'new_rows': len(planned['new_rows']),
            'update_rows': len(planned['update_rows']), 'output': canonical_output,
            'reused_active_job': canonical_output != str(output)}


def collect_one(asin, output, scope, coordinator_root):
    node = shutil.which('node')
    if not node:
        raise ApiError('NODE_18_REQUIRED')
    args = [node, str(Path(__file__).with_name('competitor.cjs')), 'collect', asin,
            '--out', str(output), '--as-of', scope['end']]
    env = dict(os.environ, AMAZON_COMPETITOR_COORDINATION_DIR=str(coordinator_root))
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', env=env, timeout=1100)
        public = json.loads(result.stdout if result.stdout.strip() else result.stderr.splitlines()[-1])
    except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
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


def process_claim(queue, job, schema, claim, client, collector, emit):
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
                return 'verification_required' if provider['state'] == 'verification' else 'authorization_pending'
            status = collector(asin, attempt, job['window'], queue.root)
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


def work(queue, job_id, *, client_factory=FeishuClient, collector=collect_one, max_items=None,
         concurrency=MAX_ACTIVE_ASINS, emit=lambda _: None):
    if type(concurrency) is not int or not 1 <= concurrency <= MAX_ACTIVE_ASINS:
        raise ApiError('CONCURRENCY_MUST_BE_1_TO_3')
    job = queue.job(job_id)
    schema = json.loads((Path(job['output']) / 'fields.json').read_text())
    provider = ProviderGate(queue.root).state()
    if provider['state'] != 'ready':
        return {'status': 'verification_required' if provider['state'] == 'verification' else 'authorization_pending',
                'job_id': job_id, 'provider': provider, 'processed': 0, 'concurrency': concurrency}
    queue.resume(job_id)
    # Each item has its own CLI client/output and claim. Only event output is shared.
    output_lock = threading.Lock()
    def publish(value):
        with output_lock:
            emit(value)

    def run_claim(claim):
        try:
            client = client_factory(Path(job['output']) / 'writeback' / uuid.uuid4().hex)
            return process_claim(queue, job, schema, claim, client, collector, publish)
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
                    stopped = 'verification_required' if provider['state'] == 'verification' else 'authorization_pending'
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'work', 'status'))
    parser.add_argument('--coord-dir')
    parser.add_argument('--url')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--asin', action='append')
    parser.add_argument('--as-of')
    parser.add_argument('--job-id')
    parser.add_argument('--max-items', type=int)
    parser.add_argument('--concurrency', type=int, choices=range(1, MAX_ACTIVE_ASINS + 1),
                        default=MAX_ACTIVE_ASINS, help='ASIN workers per batch; same-host global cap is 3')
    args = parser.parse_args()
    try:
        queue = WorkQueue(args.coord_dir)
        if args.command == 'plan':
            if not args.url or not args.out:
                raise ApiError('PLAN_URL_AND_OUTPUT_REQUIRED')
            if args.out.exists() and any(args.out.iterdir()):
                raise ApiError('OUTPUT_ALREADY_HAS_RESULTS_USE_NEW_DIRECTORY')
            asins = [parse_asin(value) for value in args.asin] if args.asin else None
            result = plan(FeishuClient(args.out), queue, args.url, args.out, asins=asins, as_of=args.as_of)
        else:
            if not args.job_id:
                if args.command != 'status':
                    raise ApiError('JOB_ID_REQUIRED')
                print(json.dumps({'active_jobs': queue.active_jobs()}, ensure_ascii=False))
                return 0
            if args.max_items is not None and args.max_items <= 0:
                raise ApiError('MAX_ITEMS_MUST_BE_POSITIVE')
            result = queue.status(args.job_id) if args.command == 'status' else work(
                queue, args.job_id, max_items=args.max_items, concurrency=args.concurrency,
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
