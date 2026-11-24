"""Same-host work claims and provider serialization; no browser or credentials."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from competitor_core import ApiError, write_json

MAX_ACTIVE_ASINS = 3


def coordination_root(supplied=None):
    return Path(supplied or os.environ.get('AMAZON_COMPETITOR_COORDINATION_DIR')
                or Path.home() / '.amazon-skill/state/amazon-jp-competitor-tracking').expanduser().resolve()


class FileMutex:
    """Kernel-owned lock: a dead worker releases ownership without a lease timeout."""
    def __init__(self, root, key, timeout=30):
        self.directory = Path(root) / 'locks'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / (hashlib.sha256(key.encode()).hexdigest() + '.lock')
        self.timeout, self.fd = timeout, None

    def acquire(self, blocking=True):
        if self.fd is not None:
            raise ApiError('LOCK_ALREADY_HELD')
        flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(self.path, flags, 0o600)
        deadline = time.monotonic() + (self.timeout if blocking else 0)
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b'0')
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fd = fd
                return True
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(.05)
            except BaseException:
                os.close(fd)
                raise

    def release(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            try:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def __enter__(self):
        if not self.acquire():
            raise ApiError('PROVIDER_BUSY')
        return self

    def __exit__(self, *args):
        self.release()


class ProviderGate:
    """One managed SellerSprite request at a time, including token refresh."""
    def __init__(self, root=None, *, clock=time.time, sleep=time.sleep):
        self.root = coordination_root(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.mutex = FileMutex(self.root, 'sellersprite-provider')
        self.local = threading.RLock()
        self.depth = 0
        self.clock, self.sleep = clock, sleep

    def state(self):
        path = self.root / 'provider.json'
        if not path.exists():
            return {'state': 'ready'}
        try:
            value = json.loads(path.read_text())
            if value.get('state') not in ('ready', 'verification', 'authorization'):
                raise ValueError()
            return value
        except (ValueError, OSError, AttributeError):
            raise ApiError('INVALID_COORDINATION_STATE') from None

    @contextmanager
    def hold(self, *, allow_paused=False):
        with self.local:
            outer = self.depth == 0
            if outer and not self.mutex.acquire():
                raise ApiError('PROVIDER_BUSY')
            self.depth += 1
            try:
                if outer and not allow_paused:
                    state = self.state()['state']
                    if state != 'ready':
                        raise ApiError('PROVIDER_VERIFICATION_PENDING' if state == 'verification'
                                       else 'PROVIDER_AUTHORIZATION_PENDING')
                yield
            finally:
                self.depth -= 1
                if outer:
                    self.mutex.release()

    def pace(self, interval):
        if not self.depth:
            raise ApiError('PROVIDER_LOCK_REQUIRED')
        path = self.root / 'rate.json'
        try:
            last = json.loads(path.read_text()).get('next_request_at', 0) if path.exists() else 0
            delay = max(0, float(last) - self.clock())
            if delay > 61:
                raise ApiError('COORDINATION_CLOCK_SKEW')
            if delay:
                self.sleep(delay)
            write_json(path, {'next_request_at': self.clock() + interval})
        except (ValueError, TypeError):
            raise ApiError('INVALID_COORDINATION_STATE') from None

    def pause(self, reason, kind):
        if not self.depth:
            raise ApiError('PROVIDER_LOCK_REQUIRED')
        write_json(self.root / 'provider.json', {'state': kind, 'reason': reason, 'at': self.clock()})

    def verified(self):
        if not self.depth:
            raise ApiError('PROVIDER_LOCK_REQUIRED')
        write_json(self.root / 'provider.json', {'state': 'ready', 'verified_at': self.clock()})


class Claim:
    def __init__(self, queue, row, lock, slot):
        self.queue, self.row, self.lock, self.slot = queue, row, lock, slot

    def assert_owned(self):
        if self.lock.fd is None or self.slot.fd is None:
            raise ApiError('CLAIM_LOST')
        with self.queue.connect() as db:
            row = db.execute('SELECT owner,generation,status FROM items WHERE job_id=? AND phase=? AND asin=?',
                             self.key).fetchone()
        if not row or tuple(row) != (self.row['owner'], self.row['generation'], 'running'):
            raise ApiError('CLAIM_LOST')

    @property
    def key(self):
        return self.row['job_id'], self.row['phase'], self.row['asin']

    def checkpoint(self, value):
        self.assert_owned()
        with self.queue.connect() as db:
            changed = db.execute('UPDATE items SET checkpoint=? WHERE job_id=? AND phase=? AND asin=? '
                'AND owner=? AND generation=? AND status="running"',
                (json.dumps(value, ensure_ascii=False), *self.key, self.row['owner'], self.row['generation'])).rowcount
        if changed != 1:
            raise ApiError('CLAIM_LOST')
        self.row['checkpoint'] = value

    def finish(self, status, category=None):
        if status not in ('done', 'partial', 'failed', 'blocked', 'pending', 'stale'):
            raise ApiError('INVALID_TASK_STATUS')
        self.assert_owned()
        with self.queue.connect() as db:
            changed = db.execute('UPDATE items SET status=?,error=? WHERE job_id=? AND phase=? AND asin=? '
                'AND owner=? AND generation=? AND status="running"',
                (status, category, *self.key, self.row['owner'], self.row['generation'])).rowcount
        if changed != 1:
            raise ApiError('CLAIM_LOST')

    def close(self):
        try:
            self.lock.release()
        finally:
            self.slot.release()


class WorkQueue:
    def __init__(self, root=None):
        self.root = coordination_root(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'jobs.sqlite3'
        if self.path.is_symlink():
            raise ApiError('UNSAFE_COORDINATION_PATH')
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, target TEXT NOT NULL,
                    window TEXT NOT NULL, output TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS items(job_id TEXT, phase INTEGER, asin TEXT,
                    position INTEGER NOT NULL, records TEXT NOT NULL, status TEXT NOT NULL,
                    owner TEXT, generation INTEGER NOT NULL DEFAULT 0, checkpoint TEXT NOT NULL DEFAULT '{}',
                    error TEXT, PRIMARY KEY(job_id,phase,asin));
                CREATE TABLE IF NOT EXISTS snapshots(job_id TEXT, asin TEXT, path TEXT NOT NULL,
                    sha256 TEXT NOT NULL, reusable INTEGER NOT NULL, PRIMARY KEY(job_id,asin));
            ''')
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, target, window, output, records, plan, *, join_active_window=False):
        job_id = 'job_' + uuid.uuid4().hex
        output = str(Path(output).resolve())
        by_id = {row['record_id']: row for row in records}
        if len(by_id) != len(records):
            raise ApiError('DUPLICATE_FEISHU_RECORD_ID')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute('SELECT * FROM jobs WHERE EXISTS (SELECT 1 FROM items '
                'WHERE items.job_id=jobs.id AND status IN ("pending","running","blocked"))').fetchall()
            for previous in active:
                old_target = json.loads(previous['target'])
                if any(old_target.get(k) != target.get(k) for k in ('base_token', 'table_id')):
                    continue
                old_records = db.execute('SELECT records FROM items WHERE job_id=?', (previous['id'],)).fetchall()
                old_ids = {row['record_id'] for group in old_records for row in json.loads(group['records'])}
                if old_ids & set(by_id):
                    old_window = json.loads(previous['window'])
                    same_window = old_window == window or (join_active_window and
                        old_window.get('start') == window.get('start') and
                        old_window.get('end', '')[:10] == window.get('end', '')[:10] and
                        old_window.get('basis') == window.get('basis'))
                    if old_ids == set(by_id) and same_window:
                        return previous['id']
                    error = ApiError('FEISHU_RECORD_ALREADY_QUEUED')
                    error.active_job_id = previous['id']
                    raise error
            db.execute('INSERT INTO jobs VALUES(?,?,?,?)',
                       (job_id, json.dumps(target), json.dumps(window), output))
            for phase, name in enumerate(('new_rows', 'update_rows')):
                grouped = {}
                for row in plan[name]:
                    grouped.setdefault(row['asin'], []).append(by_id[row['record_id']])
                for position, (asin, rows) in enumerate(grouped.items()):
                    db.execute('INSERT INTO items(job_id,phase,asin,position,records,status) VALUES(?,?,?,?,?,"pending")',
                               (job_id, phase, asin, position, json.dumps(rows, ensure_ascii=False)))
        return job_id

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise ApiError('JOB_NOT_FOUND')
        return dict(id=row['id'], target=json.loads(row['target']), window=json.loads(row['window']),
                    output=row['output'])

    def claim(self, job_id):
        self.job(job_id)
        claimed, slot = None, None
        for index in range(MAX_ACTIVE_ASINS):
            candidate = FileMutex(self.root, 'active-asin-slot:' + str(index))
            if candidate.acquire(blocking=False):
                slot = candidate
                break
        if slot is None:
            return None
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                phase = 0 if db.execute('SELECT 1 FROM items WHERE job_id=? AND phase=0 '
                    'AND status IN ("pending","running","blocked") LIMIT 1', (job_id,)).fetchone() else 1
                rows = db.execute('SELECT * FROM items WHERE job_id=? AND phase=? '
                    'AND status IN ("pending","running") ORDER BY position', (job_id, phase)).fetchall()
                for item in rows:
                    # Same ASIN is also exclusive across different jobs on this host.
                    lock = FileMutex(self.root, 'asin:JP:' + item['asin'])
                    if not lock.acquire(blocking=False):
                        continue
                    try:
                        owner, generation = uuid.uuid4().hex, item['generation'] + 1
                        db.execute('UPDATE items SET status="running",owner=?,generation=? '
                                   'WHERE job_id=? AND phase=? AND asin=?',
                                   (owner, generation, job_id, phase, item['asin']))
                        row = dict(item)
                        row.update(status='running', owner=owner, generation=generation,
                                   records=json.loads(item['records']), checkpoint=json.loads(item['checkpoint']))
                        claimed = Claim(self, row, lock, slot)
                        break
                    except BaseException:
                        lock.release()
                        raise
            if claimed is None:
                slot.release()
            return claimed
        except BaseException:
            # A failed SQLite commit must not leave an unreturned live file lock.
            if claimed is not None:
                claimed.close()
            else:
                slot.release()
            raise

    def snapshot(self, job_id, asin):
        with self.connect() as db:
            row = db.execute('SELECT * FROM snapshots WHERE job_id=? AND asin=? AND reusable=1',
                             (job_id, asin)).fetchone()
        return dict(row) if row else None

    def save_snapshot(self, claim, path, reusable):
        claim.assert_owned()
        path = Path(path).resolve()
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?)',
                (claim.row['job_id'], claim.row['asin'], str(path), hashlib.sha256(path.read_bytes()).hexdigest(), int(reusable)))

    def resume(self, job_id):
        if ProviderGate(self.root).state()['state'] != 'ready':
            raise ApiError('PROVIDER_VERIFICATION_PENDING')
        with self.connect() as db:
            db.execute('UPDATE items SET status="pending",error=NULL WHERE job_id=? AND status="blocked"', (job_id,))

    def active_jobs(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,target,output FROM jobs WHERE EXISTS (SELECT 1 FROM items '
                'WHERE items.job_id=jobs.id AND status IN ("pending","running","blocked"))').fetchall()
        return [{'job_id': row['id'], 'target': json.loads(row['target']), 'output': row['output']} for row in rows]

    def status(self, job_id):
        self.job(job_id)
        with self.connect() as db:
            rows = db.execute('SELECT phase,asin,status,owner,generation,error FROM items WHERE job_id=? ORDER BY phase,position',
                              (job_id,)).fetchall()
        return {'job_id': job_id, 'max_active_asins': MAX_ACTIVE_ASINS, 'provider': ProviderGate(self.root).state(), 'items': [dict(row) for row in rows]}
