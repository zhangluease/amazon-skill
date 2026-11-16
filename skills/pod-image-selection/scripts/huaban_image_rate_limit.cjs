const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { setTimeout: sleep } = require('node:timers/promises');
const { stateRoot } = require('./sellersprite_session.cjs');

const MIN_DELAY_MS = 4000, MAX_DELAY_MS = 6000;

function rateError(code) { return Object.assign(Error(code), { code }); }

async function acquireImageSearchGate(directory = stateRoot(), dependencies = {}) {
  const now = dependencies.now || Date.now;
  const pause = dependencies.sleep || sleep;
  const randomInt = dependencies.randomInt || crypto.randomInt;
  const lock = path.join(directory, 'huaban-image-search.lock');
  const state = path.join(directory, 'huaban-image-search-rate.json');
  const token = crypto.randomUUID();
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  const deadline = now() + 60000;
  for (;;) {
    try {
      await fs.writeFile(lock, JSON.stringify({ pid: process.pid, token }), { flag: 'wx', mode: 0o600 });
      break;
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      if (now() >= deadline) throw rateError('IMAGE_SEARCH_RATE_LIMIT_BUSY');
      let owner, observed, dead = false;
      try { observed = await fs.readFile(lock, 'utf8'); owner = JSON.parse(observed); } catch {}
      if (Number.isInteger(owner?.pid) && owner.pid > 0) {
        try { process.kill(owner.pid, 0); } catch (error) { dead = error.code === 'ESRCH'; }
      } else {
        const stat = await fs.stat(lock).catch(() => null);
        dead = Boolean(stat && now() - stat.mtimeMs > 5000);
      }
      if (dead) {
        const current = await fs.readFile(lock, 'utf8').catch(() => null);
        if (current !== null && current === observed)
          await fs.unlink(lock).catch(error => { if (error.code !== 'ENOENT') throw error; });
        continue;
      }
      await pause(100);
    }
  }
  let released = false, completed = false;
  const release = async () => {
    if (released) return;
    const owner = JSON.parse(await fs.readFile(lock, 'utf8'));
    if (owner.token !== token) throw rateError('IMAGE_SEARCH_RATE_LIMIT_LOCK_LOST');
    await fs.unlink(lock);
    released = true;
  };
  const timing = { min_delay_ms: MIN_DELAY_MS, max_delay_ms: MAX_DELAY_MS, waited_ms: 0 };
  try {
    let prior;
    try { prior = JSON.parse(await fs.readFile(state, 'utf8')); }
    catch (error) { if (error.code !== 'ENOENT') throw rateError('IMAGE_SEARCH_RATE_LIMIT_STATE_INVALID'); }
    if (prior && (!Number.isSafeInteger(prior.next_allowed_at_ms) || prior.next_allowed_at_ms < 0))
      throw rateError('IMAGE_SEARCH_RATE_LIMIT_STATE_INVALID');
    const waitStarted = now();
    while (prior && prior.next_allowed_at_ms > now())
      await pause(Math.min(prior.next_allowed_at_ms - now(), MAX_DELAY_MS));
    timing.waited_ms = prior ? Math.max(0, now() - waitStarted) : 0;
    return {
      timing,
      async complete() {
        if (completed || released) return;
        const completedAt = now(), cooldown = randomInt(MIN_DELAY_MS, MAX_DELAY_MS + 1);
        const record = { completed_at_ms: completedAt, cooldown_ms: cooldown,
          next_allowed_at_ms: completedAt + cooldown };
        const temporary = state + '.' + token + '.tmp';
        try {
          await fs.writeFile(temporary, JSON.stringify(record), { flag: 'wx', mode: 0o600 });
          await fs.rename(temporary, state);
        } finally { await fs.rm(temporary, { force: true }); }
        completed = true;
        Object.assign(timing, { cooldown_ms: cooldown, next_allowed_at_ms: record.next_allowed_at_ms });
        await release();
      },
      release
    };
  } catch (error) { await release(); throw error; }
}

module.exports = { acquireImageSearchGate, MIN_DELAY_MS, MAX_DELAY_MS };
