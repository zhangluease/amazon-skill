const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { acquireImageSearchGate } = require('../skills/pod-image-selection/scripts/huaban_image_rate_limit.cjs');

async function fixture(t, values = [4000]) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'huaban-image-rate-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const clock = { value: 100000 }, pauses = [], bounds = [];
  return { directory, clock, pauses, bounds, dependencies: {
    now: () => clock.value,
    sleep: async ms => { pauses.push(ms); clock.value += ms; },
    randomInt: (min, max) => { bounds.push([min, max]); return values.shift(); }
  } };
}

test('cooldown starts when results finish, and a final image adds no trailing sleep', async t => {
  const f = await fixture(t);
  const first = await acquireImageSearchGate(f.directory, f.dependencies);
  assert.deepEqual(f.pauses, []);
  f.clock.value += 9000; // Time spent collecting the image results.
  await first.complete();
  assert.deepEqual(f.pauses, []);
  const record = JSON.parse(await fs.readFile(path.join(f.directory, 'huaban-image-search-rate.json')));
  assert.equal(record.completed_at_ms, 109000);
  assert.equal(record.next_allowed_at_ms, 113000);
  const second = await acquireImageSearchGate(f.directory, f.dependencies);
  assert.deepEqual(f.pauses, [4000]);
  assert.equal(second.timing.waited_ms, 4000);
  await second.release();
});

test('each completed image draws a fresh 4–6 second delay and only the remainder is awaited', async t => {
  const f = await fixture(t, [6000, 4871, 4000]);
  const first = await acquireImageSearchGate(f.directory, f.dependencies);
  await first.complete();
  f.clock.value += 2500;
  const second = await acquireImageSearchGate(f.directory, f.dependencies);
  assert.deepEqual(f.pauses, [3500]);
  await second.complete();
  assert.equal(second.timing.cooldown_ms, 4871);
  f.clock.value += 5000;
  const third = await acquireImageSearchGate(f.directory, f.dependencies);
  assert.equal(third.timing.waited_ms, 0);
  await third.complete();
  assert.equal(third.timing.cooldown_ms, 4000);
  assert.deepEqual(f.bounds, [[4000, 6001], [4000, 6001], [4000, 6001]]);
});

test('a concurrent image cannot acquire the upload gate until the previous image finishes', async t => {
  const f = await fixture(t);
  const first = await acquireImageSearchGate(f.directory, f.dependencies);
  let previousFinished = false;
  const second = await acquireImageSearchGate(f.directory, { ...f.dependencies, sleep: async ms => {
    if (!previousFinished) {
      assert.equal(ms, 100); // Waiting for the active search, not starting its cooldown early.
      await first.complete();
      previousFinished = true;
    }
    f.clock.value += ms;
  } });
  assert.equal(previousFinished, true);
  assert.equal(f.clock.value, 104000);
  assert.equal(second.timing.waited_ms, 3900);
  await second.release();
});

test('a fresh Node process observes the previous image cooldown', async t => {
  const f = await fixture(t);
  const first = await acquireImageSearchGate(f.directory, f.dependencies);
  await first.complete();
  const source = `
    const { acquireImageSearchGate } = require(process.argv[1]);
    let now = Number(process.argv[3]);
    (async () => {
      const gate = await acquireImageSearchGate(process.argv[2], {
        now: () => now, sleep: async ms => { now += ms; }
      });
      await gate.release();
      process.stdout.write(JSON.stringify({ waited: gate.timing.waited_ms }));
    })().catch(() => { process.exitCode = 1; });`;
  const result = await promisify(execFile)(process.execPath, ['-e', source,
    require.resolve('../skills/pod-image-selection/scripts/huaban_image_rate_limit.cjs'),
    f.directory, String(f.clock.value)]);
  assert.equal(JSON.parse(result.stdout).waited, 4000);
});

test('failed or cancelled work releases its gate, and corrupt state never bypasses frequency control', async t => {
  const f = await fixture(t);
  const gate = await acquireImageSearchGate(f.directory, f.dependencies);
  await gate.release();
  await gate.release();
  await assert.rejects(fs.access(path.join(f.directory, 'huaban-image-search.lock')));
  await fs.writeFile(path.join(f.directory, 'huaban-image-search-rate.json'), 'invalid');
  await assert.rejects(() => acquireImageSearchGate(f.directory, f.dependencies),
    { code: 'IMAGE_SEARCH_RATE_LIMIT_STATE_INVALID' });
  await assert.rejects(fs.access(path.join(f.directory, 'huaban-image-search.lock')));
});

test('an abandoned empty lock can recover without skipping the cooldown check', async t => {
  const f = await fixture(t);
  const lock = path.join(f.directory, 'huaban-image-search.lock');
  await fs.writeFile(lock, '');
  await fs.utimes(lock, new Date(0), new Date(0));
  const gate = await acquireImageSearchGate(f.directory, f.dependencies);
  await gate.release();
  await assert.rejects(fs.access(lock));
});

test('a busy upload gate times out without removing an active owner lock', async t => {
  const f = await fixture(t);
  const lock = path.join(f.directory, 'huaban-image-search.lock');
  const owner = JSON.stringify({ pid: process.pid, token: 'active-other-search' });
  await fs.writeFile(lock, owner);
  await assert.rejects(() => acquireImageSearchGate(f.directory, f.dependencies),
    { code: 'IMAGE_SEARCH_RATE_LIMIT_BUSY' });
  assert.equal(await fs.readFile(lock, 'utf8'), owner);
});
