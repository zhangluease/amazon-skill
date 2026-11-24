"""Real process locks and queue fencing, with all vendor/Feishu traffic replaced offline."""
import copy
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
import time
from pathlib import Path
import queue as queue_module
import sys
import tempfile
import threading
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/amazon-jp-competitor-tracking/scripts'
sys.path.insert(0, str(SCRIPTS))
from competitor_core import ApiError, Client, window, write_json
from competitor_coordination import WorkQueue, ProviderGate, FileMutex
from feishu_plan import create_plan
from feishu_sync import plan, work
from feishu_cli import FeishuClient
sys.path.pop(0)
from test_competitor_session import SESSION, COMPLETE, PRODUCT, Opener, ok
from test_feishu_competitor import SCHEMA, URL, product

A, B, C = 'B07RPLXMXD', 'B008H0V15I', 'B0CDCD694T'
TARGET = {'base_token': 'baseDemo', 'table_id': 'tblDemo', 'view_id': 'vewDemo'}
SCOPE = window('2026-11-24')


def rows_for(asins):
    return [{'record_id': 'rec' + str(i), 'ASIN': asin, '标题': None} for i, asin in enumerate(asins)]


def make_job(queue, output, rows, target=TARGET):
    Path(output).mkdir(parents=True, exist_ok=True)
    write_json(Path(output) / 'fields.json', SCHEMA)
    planned = create_plan(rows, URL, columns=[x['name'] for x in SCHEMA])
    return queue.create(target, SCOPE, output, rows, planned)


def claim_until_done(root, job, barrier, results):
    q = WorkQueue(root)
    barrier.wait(5)
    while True:
        claim = q.claim(job)
        if claim is None:
            return
        results.put((claim.row['asin'], claim.row['owner']))
        claim.finish('done')
        claim.close()


def claim_and_hold(root, job, start, release, results):
    queue = WorkQueue(root)
    start.wait(5)
    claim = queue.claim(job)
    results.put(claim.row['asin'] if claim else None)
    if claim:
        try:
            release.wait(10)
            claim.finish('done')
        finally:
            claim.close()


def hold_claim(root, job, connection):
    claim = WorkQueue(root).claim(job)
    connection.send((claim.row['asin'], claim.row['generation']))
    connection.recv()
    claim.close()


def hold_provider(root, entered, release):
    with ProviderGate(root).hold():
        entered.put(True)
        release.wait(5)


def run_cli_with_inherited_lock(root, binary, marker, release):
    with FileMutex(root, 'inflight-cli-write') as lock:
        client = FeishuClient(Path(root) / 'cli-output', binary)
        client.inherited_lock_fds = (lock.fd,)
        client.run(['--marker', str(marker), '--release', str(release)])


class Remote:
    """The fake server persists across worker restarts, unlike per-process client objects."""
    def __init__(self, rows):
        self.rows, self.events, self.writes = copy.deepcopy(rows), [], []
        self.crash_after_update = False

    def client(self, output):
        remote = self
        class Cli:
            def __init__(self):
                self.output = Path(output)
            def resolve(self, url):
                return TARGET
            def fields(self, target):
                return SCHEMA
            def records(self, target, schema, record_ids=None):
                return copy.deepcopy([r for r in remote.rows if record_ids is None or r['record_id'] in record_ids])
            def update(self, target, updates):
                names = {f['id']: f['name'] for f in SCHEMA}
                remote.writes.append(copy.deepcopy(updates))
                for row in remote.rows:
                    if row['record_id'] in updates:
                        remote.events.append(('write', row['ASIN'], row['record_id']))
                        row.update({names[k]: v for k, v in updates[row['record_id']].items()})
                if remote.crash_after_update:
                    remote.crash_after_update = False
                    raise SystemExit('simulated process death after server commit')
        return Cli()

    def collect(self, asin, output, scope, root):
        self.events.append(('collect', asin))
        value = dict(product(), asin=asin, title='Fixture ' + asin, brand=asin, status='ok')
        write_json(Path(output) / 'products.json', {'format': 'amazon-jp-competitor-tracking-v1',
                   'requested_asins': [asin], 'window': scope, 'products': [value]})
        return {'status': 'ok'}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.queue = WorkQueue(self.root / 'coord')
        self.ctx = multiprocessing.get_context('spawn')

class CoordinationTests(Fixture):
    def test_two_processes_claim_each_asin_only_once(self):
        job = make_job(self.queue, self.root / 'out', rows_for([A, B, C]))
        barrier, results = self.ctx.Event(), self.ctx.Queue()
        children = [self.ctx.Process(target=claim_until_done, args=(self.queue.root, job, barrier, results)) for _ in range(2)]
        for child in children:
            child.start()
        barrier.set()
        for child in children:
            child.join(8)
            self.assertEqual(child.exitcode, 0)
        claimed = [results.get(timeout=2)[0] for _ in range(3)]
        self.assertCountEqual(claimed, [A, B, C])
        self.assertTrue(all(x['status'] == 'done' for x in self.queue.status(job)['items']))

    def test_six_processes_across_jobs_share_three_active_slots(self):
        asins = [f'B00000000{i}' for i in range(1, 7)]
        jobs = [make_job(self.queue, self.root / f'out{i}', rows_for(asins[i*3:(i+1)*3]),
                         dict(TARGET, table_id=f'tbl{i}')) for i in range(2)]
        start, release, results = self.ctx.Event(), self.ctx.Event(), self.ctx.Queue()
        children = [self.ctx.Process(target=claim_and_hold,
                    args=(self.queue.root, jobs[i//3], start, release, results)) for i in range(6)]
        for child in children:
            child.start()
        try:
            start.set()
            values = [results.get(timeout=5) for _ in children]
            self.assertEqual(sum(value is not None for value in values), 3)
            self.assertEqual(len({value for value in values if value}), 3)
            self.assertEqual(sum(item['status'] == 'running' for job in jobs
                                 for item in self.queue.status(job)['items']), 3)
        finally:
            release.set()
            for child in children:
                child.join(5)
                if child.is_alive():
                    child.kill()
                    child.join(5)
        self.assertTrue(all(child.exitcode == 0 for child in children))

    def test_empty_claims_do_not_leak_slots_and_close_releases_capacity(self):
        empty = make_job(self.queue, self.root / 'empty', [])
        for _ in range(5):
            self.assertIsNone(self.queue.claim(empty))
        job = make_job(self.queue, self.root / 'out', rows_for([A, B, C, 'B000000001']))
        claims = [self.queue.claim(job) for _ in range(3)]
        self.assertTrue(all(claims))
        self.assertIsNone(self.queue.claim(job))
        first = claims.pop(0)
        first.finish('done')
        first.close()
        replacement = self.queue.claim(job)
        self.assertEqual(replacement.row['asin'], 'B000000001')
        for claim in claims + [replacement]:
            claim.finish('done')
            claim.close()

    def test_live_owner_is_not_stolen_and_dead_owner_is_reclaimed_with_new_generation(self):
        job = make_job(self.queue, self.root / 'out', rows_for([A]))
        parent, child = self.ctx.Pipe()
        worker = self.ctx.Process(target=hold_claim, args=(self.queue.root, job, child))
        worker.start()
        self.addCleanup(lambda: worker.kill() if worker.is_alive() else None)
        self.assertTrue(parent.poll(5))
        _, generation = parent.recv()
        self.assertIsNone(self.queue.claim(job))
        worker.kill()
        worker.join(5)
        claim = self.queue.claim(job)
        self.assertEqual(claim.row['generation'], generation + 1)
        claim.finish('done')
        claim.close()

    def test_same_asin_exclusive_across_jobs_and_lost_claim_cannot_write(self):
        job = make_job(self.queue, self.root / 'first', rows_for([A]))
        other = make_job(self.queue, self.root / 'other', rows_for([A]), dict(TARGET, table_id='tblOther'))
        claim = self.queue.claim(job)
        self.assertIsNone(self.queue.claim(other))
        with self.queue.connect() as db:
            db.execute('UPDATE items SET generation=generation+1 WHERE job_id=?', (job,))
        with self.assertRaisesRegex(ApiError, 'CLAIM_LOST'):
            claim.checkpoint({'bad': True})
        claim.close()
        second = self.queue.claim(other)
        second.finish('done')
        second.close()

    def test_old_rows_wait_for_all_running_new_rows(self):
        rows = rows_for([A, B, C])
        rows[2]['标题'] = 'Existing'
        job = make_job(self.queue, self.root / 'out', rows)
        first, second = self.queue.claim(job), self.queue.claim(job)
        first.finish('done')
        first.close()
        self.assertIsNone(self.queue.claim(job))
        second.finish('done')
        second.close()
        old = self.queue.claim(job)
        self.assertEqual((old.row['phase'], old.row['asin']), (1, C))
        old.finish('done')
        old.close()

    def test_identical_active_plans_join_one_job_and_overlap_is_rejected(self):
        rows = rows_for([A, B])
        remote = Remote(rows)
        one = plan(remote.client(self.root / 'p1'), self.queue, URL, self.root / 'p1', as_of='2026-11-24')
        two = plan(remote.client(self.root / 'p2'), self.queue, URL, self.root / 'p2', as_of='2026-11-24')
        self.assertEqual(one['job_id'], two['job_id'])
        self.assertTrue(two['reused_active_job'])
        self.assertEqual(two['output'], one['output'])
        with self.assertRaisesRegex(ApiError, 'FEISHU_RECORD_ALREADY_QUEUED'):
            make_job(self.queue, self.root / 'p3', rows[:1])
        self.assertEqual(len(self.queue.active_jobs()), 1)

    def test_default_plans_join_active_batch_despite_milliseconds_between_reads(self):
        remote = Remote(rows_for([A]))
        one = plan(remote.client(self.root / 'p1'), self.queue, URL, self.root / 'p1')
        two = plan(remote.client(self.root / 'p2'), self.queue, URL, self.root / 'p2')
        self.assertEqual(one['job_id'], two['job_id'])

    def test_shared_provider_pause_prevents_requests_until_verified_by_api(self):
        gate = ProviderGate(self.queue.root, sleep=lambda _: None)
        opener = Opener([{'code': 'ERR_ROBOT_CHECK'}])
        first = Client(copy.deepcopy(SESSION), opener=opener, sleep=lambda _: None, gate=gate)
        with self.assertRaisesRegex(ApiError, 'ERR_ROBOT_CHECK'):
            first.get(PRODUCT, {'asins': A})
        untouched = Opener([ok([COMPLETE])])
        fresh = Client(copy.deepcopy(SESSION), opener=untouched, sleep=lambda _: None,
                       gate=ProviderGate(self.queue.root, sleep=lambda _: None))
        with self.assertRaisesRegex(ApiError, 'PROVIDER_VERIFICATION_PENDING'):
            fresh.get(PRODUCT, {'asins': A})
        self.assertEqual(untouched.requests, [])
        checked = Client(copy.deepcopy(SESSION), opener=Opener([ok({'token': SESSION['headers']['auth-token']})]),
                         sleep=lambda _: None, gate=gate)
        checked.verify_session()
        self.assertEqual(fresh.get(PRODUCT, {'asins': A})[0]['asin'], A)
        self.assertEqual(gate.state()['state'], 'ready')

    @unittest.skipIf(os.name == 'nt', 'POSIX inherited file descriptors')
    def test_inflight_cli_keeps_lock_after_parent_process_dies(self):
        marker, release = self.root / 'started', self.root / 'release'
        binary = self.root / 'offline-cli'
        binary.write_text('#!/usr/bin/env python3\n' + """import json, pathlib, sys, time
assert sys.argv[-2:] == ['--as', 'user']
marker = pathlib.Path(sys.argv[sys.argv.index('--marker')+1])
release = pathlib.Path(sys.argv[sys.argv.index('--release')+1])
marker.write_text('started')
end = time.monotonic() + 8
while not release.exists() and time.monotonic() < end:
    time.sleep(.02)
print(json.dumps({'ok': True, 'identity': 'user', 'data': {}}))
""")
        binary.chmod(0o700)
        worker = self.ctx.Process(target=run_cli_with_inherited_lock,
                                  args=(self.queue.root, binary, marker, release))
        worker.start()
        self.addCleanup(lambda: worker.kill() if worker.is_alive() else None)
        self.addCleanup(lambda: release.touch())
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(marker.exists())
        worker.kill()
        worker.join(5)
        contender = FileMutex(self.queue.root, 'inflight-cli-write', timeout=5)
        self.assertFalse(contender.acquire(blocking=False))
        release.touch()
        self.assertTrue(contender.acquire())
        contender.release()

    def test_pacing_persists_between_fresh_client_instances(self):
        now, starts = [1000.0], []
        def sleep(delay):
            now[0] += delay
        class TimedOpener(Opener):
            def open(inner, request, timeout):
                starts.append(now[0])
                return super().open(request, timeout)
        for _ in range(3):
            client = Client(copy.deepcopy(SESSION), opener=TimedOpener([ok([COMPLETE])]),
                            sleep=lambda _: None,
                            gate=ProviderGate(self.queue.root, clock=lambda: now[0], sleep=sleep))
            client.get(PRODUCT, {'asins': A})
        self.assertEqual(starts, [1000.0, 1001.0, 1002.0])

    def test_provider_lock_serializes_different_processes(self):
        entered1, entered2, release = self.ctx.Queue(), self.ctx.Queue(), self.ctx.Event()
        workers = [self.ctx.Process(target=hold_provider, args=(self.queue.root, q, release)) for q in (entered1, entered2)]
        workers[0].start()
        self.assertTrue(entered1.get(timeout=5))
        workers[1].start()
        with self.assertRaises(queue_module.Empty):
            entered2.get(timeout=.25)
        release.set()
        self.assertTrue(entered2.get(timeout=5))
        for child in workers:
            child.join(5)
            self.assertEqual(child.exitcode, 0)


class SyncTests(Fixture):
    def test_collect_then_write_one_before_collecting_next_and_old_phase_reuses_snapshot(self):
        rows = rows_for([A, B, A])
        rows[2]['标题'] = 'Existing A'
        job = make_job(self.queue, self.root / 'out', rows)
        remote = Remote(rows)
        result = work(self.queue, job, client_factory=remote.client, collector=remote.collect, concurrency=1)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(remote.events, [('collect', A), ('write', A, 'rec0'),
                         ('collect', B), ('write', B, 'rec1'), ('write', A, 'rec2')])
        self.assertEqual([row['品牌'] for row in remote.rows], [A, B, A])
        self.assertEqual(remote.rows[2]['标题'], 'Fixture ' + A)

    def test_default_three_item_batches_write_individually_and_wait_before_next_batch(self):
        rows = rows_for([A, B, C, 'B000000001', 'B000000002'])
        job = make_job(self.queue, self.root / 'out', rows)
        remote = Remote(rows)
        barrier, release, fast_written = threading.Barrier(3), threading.Event(), threading.Event()
        seen, events, guard = set(), [], threading.Lock()
        def client_factory(output):
            client = remote.client(output)
            records = client.records
            def read(target, schema, record_ids=None):
                record_id = record_ids[0] if record_ids and len(record_ids) == 1 else None
                with guard:
                    first = record_id in ('rec0', 'rec1', 'rec2') and record_id not in seen
                    if first:
                        seen.add(record_id)
                if first:
                    # All three work units must be active, while vendor collection remains serialized.
                    barrier.wait(timeout=5)
                    if record_id == 'rec2':
                        if not release.wait(5):
                            raise AssertionError('Test did not release the slow item')
                return records(target, schema, record_ids)
            client.records = read
            return client
        def emit(event):
            events.append(event)
            written = {e['record_id'] for e in events if e['event'] == 'record_verified'}
            if {'rec0', 'rec1'} <= written:
                fast_written.set()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(work, self.queue, job, client_factory=client_factory,
                                     collector=remote.collect, emit=emit)
            try:
                self.assertTrue(fast_written.wait(5))
                self.assertEqual({e['record_ids'][0] for e in events if e['event'] == 'claimed'},
                                 {'rec0', 'rec1', 'rec2'})
                self.assertIsNotNone(remote.rows[0]['标题'])
                self.assertIsNotNone(remote.rows[1]['标题'])
                self.assertIsNone(remote.rows[2]['标题'])
            finally:
                release.set()
            result = future.result(timeout=5)
        self.assertEqual((result['status'], result['concurrency'], result['batches'], result['processed']),
                         ('complete', 3, 2, 5))
        self.assertEqual([row['品牌'] for row in remote.rows], [row['ASIN'] for row in rows])

    def test_concurrency_above_three_rejected_and_item_limit_not_overclaimed(self):
        with self.assertRaisesRegex(ApiError, 'CONCURRENCY_MUST_BE_1_TO_3'):
            work(self.queue, 'unused', concurrency=4)
        rows = rows_for([A, B, C])
        job = make_job(self.queue, self.root / 'out', rows)
        remote = Remote(rows)
        result = work(self.queue, job, client_factory=remote.client, collector=remote.collect, max_items=1)
        self.assertEqual(result['processed'], 1)
        self.assertEqual([r['status'] for r in result['queue']['items']], ['done', 'pending', 'pending'])
        self.assertEqual(len(remote.writes), 1)

    def test_one_failure_does_not_prevent_other_asin_write(self):
        rows = rows_for([A, B])
        job = make_job(self.queue, self.root / 'out', rows)
        remote = Remote(rows)
        def collect(asin, *args):
            if asin == A:
                raise ApiError('NETWORK_FAILED')
            return remote.collect(asin, *args)
        result = work(self.queue, job, client_factory=remote.client, collector=collect)
        self.assertEqual(result['status'], 'complete_with_gaps')
        self.assertEqual([x['status'] for x in result['queue']['items']], ['failed', 'done'])
        self.assertEqual(len(remote.writes), 1)
        self.assertEqual(remote.rows[1]['品牌'], B)

    def test_stale_record_is_rejected_before_collection(self):
        rows = rows_for([A])
        job = make_job(self.queue, self.root / 'out', rows)
        remote = Remote(rows)
        remote.rows[0]['标题'] = 'Already changed by another chat'
        result = work(self.queue, job, client_factory=remote.client, collector=remote.collect)
        self.assertEqual(remote.events, [])
        self.assertEqual(result['queue']['items'][0]['status'], 'stale')

    def test_mismatched_asin_or_window_never_written(self):
        for bad in ('asin', 'window'):
            with self.subTest(bad=bad):
                rows = rows_for([A])
                root = self.root / bad
                q = WorkQueue(root / 'coord')
                job = make_job(q, root / 'out', rows)
                remote = Remote(rows)
                def collect(asin, output, scope, directory):
                    value = remote.collect(asin, output, scope, directory)
                    path = Path(output) / 'products.json'
                    import json
                    data = json.loads(path.read_text())
                    if bad == 'asin':
                        data['products'][0]['asin'] = B
                    else:
                        data['window'] = window('2026-11-23')
                    write_json(path, data)
                    return value
                result = work(q, job, client_factory=remote.client, collector=collect)
                self.assertFalse(remote.writes)
                self.assertEqual(result['queue']['items'][0]['error'], 'TASK_PRODUCT_OR_WINDOW_MISMATCH')

    def test_committed_write_recovers_after_crash_without_recollecting_or_overwriting_price_flag(self):
        rows = rows_for([A])
        rows[0]['当前售价'] = '￥100'
        rows[0]['标题'] = 'Old'
        job = make_job(self.queue, self.root / 'out', rows)
        remote = Remote(rows)
        remote.crash_after_update = True
        with self.assertRaises(SystemExit):
            work(self.queue, job, client_factory=remote.client, collector=remote.collect)
        result = work(self.queue, job, client_factory=remote.client, collector=remote.collect)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(remote.writes), 1)
        self.assertEqual(remote.rows[0]['价格波动（超10%）'], ['是'])
        self.assertEqual([x for x in remote.events if x[0] == 'collect'], [('collect', A)])

    def test_partial_data_written_then_all_workers_wait_on_verification(self):
        rows = rows_for([A, B])
        job = make_job(self.queue, self.root / 'out', rows)
        remote = Remote(rows)
        def collect(*args):
            remote.collect(*args)
            gate = ProviderGate(self.queue.root)
            with gate.hold():
                gate.pause('ERR_ROBOT_CHECK', 'verification')
            return {'status': 'verification_required', 'category': 'ERR_ROBOT_CHECK'}
        result = work(self.queue, job, client_factory=remote.client, collector=collect)
        self.assertEqual(result['status'], 'verification_required')
        self.assertEqual(len(remote.writes), 1)
        second = work(self.queue, job, client_factory=remote.client,
                      collector=lambda *args: self.fail('Must not make more requests while paused'))
        self.assertEqual(second['processed'], 0)
        # Scheduling order within a parallel batch is unspecified; exactly one item collected before pause.
        self.assertEqual(sum(row['标题'] is not None for row in remote.rows), 1)
        self.assertEqual(sum(event[0] == 'collect' for event in remote.events), 1)

    def test_snapshot_tampering_blocks_reuse_on_restart(self):
        rows = rows_for([A])
        job = make_job(self.queue, self.root / 'out', rows)
        remote = Remote(rows)
        claim = self.queue.claim(job)
        path = self.root / 'out' / 'fixture' / 'products.json'
        remote.collect(A, path.parent, SCOPE, self.queue.root)
        self.queue.save_snapshot(claim, path, True)
        claim.close()
        path.write_text(path.read_text() + ' ')
        result = work(self.queue, job, client_factory=remote.client, collector=remote.collect)
        self.assertEqual(result['queue']['items'][0]['error'], 'SNAPSHOT_CHECKSUM_MISMATCH')
        self.assertFalse(remote.writes)


if __name__ == '__main__':
    unittest.main()
