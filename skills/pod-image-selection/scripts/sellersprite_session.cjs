const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');

const STORE_ID = 'lnbmbgocenenhhhdojdielgnmeflbnfb';
const SKILL = path.resolve(__dirname, '..');
function fail(code) { return Object.assign(new Error(code), { code }); }
function inside(child, parent) { const r = path.relative(parent, child); return !r || !r.startsWith('..' + path.sep) && r !== '..' && !path.isAbsolute(r); }
async function canonical(target) {
  try { return await fs.realpath(target); } catch {
    const parent = path.dirname(target);
    return parent === target ? target : path.join(await canonical(parent), path.basename(target));
  }
}
function stateRoot(env = process.env, platform = process.platform, home = os.homedir()) {
  return path.resolve(env.POD_IMAGE_SELECTION_STATE_DIR || (platform === 'win32'
    ? path.join(env.LOCALAPPDATA || path.join(home, 'AppData', 'Local'), 'pod-image-selection')
    : platform === 'darwin' ? path.join(home, 'Library', 'Application Support', 'pod-image-selection')
      : path.join(env.XDG_DATA_HOME || path.join(home, '.local', 'share'), 'pod-image-selection')));
}
function browserRoots(env = process.env, platform = process.platform, home = os.homedir()) {
  if (platform === 'darwin') return ['Google/Chrome', 'Microsoft Edge', 'Chromium']
    .map(p => path.join(home, 'Library', 'Application Support', p));
  if (platform === 'win32') return ['Google/Chrome/User Data', 'Microsoft/Edge/User Data', 'Chromium/User Data']
    .map(p => path.join(env.LOCALAPPDATA || path.join(home, 'AppData', 'Local'), p));
  return ['google-chrome', 'microsoft-edge', 'chromium'].map(p => path.join(env.XDG_CONFIG_HOME || path.join(home, '.config'), p));
}
async function readManifest(directory, trusted = false) {
  try {
    const m = JSON.parse(await fs.readFile(path.join(directory, 'manifest.json'), 'utf8'));
    const popup = m.action?.default_popup;
    if (m.manifest_version !== 3 || !/^\d+(?:\.\d+){1,3}$/.test(m.version || '')
      || typeof popup !== 'string' || !popup.endsWith('.html') || popup.includes('..') || path.isAbsolute(popup)) return null;
    const hosts = (m.host_permissions || []).join(' ');
    if (!trusted && !hosts.includes('e.sellersprite.com')) return null;
    await fs.access(path.join(directory, popup));
    return m;
  } catch { return null; }
}
function extensionId(manifest) {
  if (!manifest.key) return null;
  return crypto.createHash('sha256').update(Buffer.from(manifest.key, 'base64')).digest('hex').slice(0, 32)
    .replace(/[0-9a-f]/g, n => String.fromCharCode(97 + parseInt(n, 16)));
}
async function discoverExtension(roots = browserRoots()) {
  const found = [];
  for (const root of roots) {
    const profiles = await fs.readdir(root, { withFileTypes: true }).catch(() => []);
    for (const profile of profiles.filter(p => p.isDirectory() && /^(Default|Profile \d+)$/.test(p.name))) {
      const base = path.join(root, profile.name, 'Extensions');
      const ids = await fs.readdir(base, { withFileTypes: true }).catch(() => []);
      ids.sort((a, b) => Number(b.name === STORE_ID) - Number(a.name === STORE_ID));
      for (const id of ids.filter(p => p.isDirectory()).slice(0, 200)) {
        const versions = await fs.readdir(path.join(base, id.name), { withFileTypes: true }).catch(() => []);
        for (const version of versions.filter(p => p.isDirectory())) {
          const directory = path.join(base, id.name, version.name), manifest = await readManifest(directory);
          if (manifest) found.push({ directory, manifest });
        }
      }
    }
  }
  found.sort((a, b) => b.manifest.version.localeCompare(a.manifest.version, undefined, { numeric: true }));
  return found[0] || null;
}
async function prepareHome(directory, output) {
  directory = await canonical(path.resolve(directory)); output = await canonical(path.resolve(output));
  // Only a newly created/explicitly managed directory can hold the dedicated profile.
  if (inside(directory, SKILL) || inside(directory, output) || inside(output, directory)
    || browserRoots().some(root => inside(directory, root))) throw fail('UNSAFE_STATE_DIRECTORY');
  for (let p = directory;; p = path.dirname(p)) {
    if (await fs.stat(path.join(p, '.git')).then(() => true).catch(() => false)) throw fail('STATE_DIRECTORY_IN_REPOSITORY');
    if (path.dirname(p) === p) break;
  }
  const existing = await fs.readdir(directory).catch(() => []);
  if (existing.length && !existing.includes('.pod-image-selection')) throw fail('STATE_DIRECTORY_NOT_MANAGED');
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  if ((await fs.lstat(directory)).isSymbolicLink()) throw fail('UNSAFE_STATE_DIRECTORY');
  for (const name of ['profile', 'extension']) {
    if (await fs.lstat(path.join(directory, name)).then(s => s.isSymbolicLink()).catch(() => false)) throw fail('UNSAFE_STATE_DIRECTORY');
  }
  await fs.chmod(directory, 0o700).catch(() => {});
  await fs.writeFile(path.join(directory, '.pod-image-selection'), '1\n', { mode: 0o600 });
  const lock = path.join(directory, '.running');
  try { await fs.mkdir(lock, { mode: 0o700 }); } catch {
    const pid = Number(await fs.readFile(path.join(lock, 'pid'), 'utf8').catch(() => ''));
    let dead = false;
    if (Number.isInteger(pid) && pid > 0) {
      try { process.kill(pid, 0); } catch (error) { dead = error.code === 'ESRCH'; }
    }
    if (!dead) throw fail('PROFILE_BUSY');
    await fs.unlink(path.join(lock, 'pid')).catch(() => {});
    await fs.rmdir(lock); await fs.mkdir(lock, { mode: 0o700 });
  }
  await fs.writeFile(path.join(lock, 'pid'), String(process.pid), { mode: 0o600 });
  return { directory, profile: path.join(directory, 'profile'), extension: path.join(directory, 'extension'),
    release: async () => { await fs.unlink(path.join(lock, 'pid')).catch(() => {}); await fs.rmdir(lock).catch(() => {}); } };
}
async function prepareExtension(home, supplied, dependencies = {}) {
  const read = dependencies.readManifest || readManifest;
  let source;
  if (supplied) {
    const directory = path.resolve(supplied), manifest = await read(directory);
    if (!manifest) throw fail('INVALID_EXTENSION_DIRECTORY');
    source = { directory, manifest };
  } else source = await (dependencies.discover || discoverExtension)();
  const cached = await read(home.extension);
  if (!source && !cached) throw fail('EXTENSION_NOT_FOUND');
  if (source && (!cached || source.manifest.version !== cached.version)) {
    const stage = path.join(home.directory, 'extension-new');
    await fs.rm(stage, { recursive: true, force: true });
    await fs.cp(source.directory, stage, { recursive: true, filter: async item => {
      if (item.split(path.sep).includes('_metadata')) return false;
      if ((await fs.lstat(item)).isSymbolicLink()) throw fail('EXTENSION_SYMLINK_NOT_SUPPORTED');
      return true;
    } });
    if (!await read(stage)) throw fail('EXTENSION_COPY_INVALID');
    await fs.rm(home.extension, { recursive: true, force: true });
    await fs.rename(stage, home.extension);
  }
  const manifest = await read(home.extension);
  return { directory: home.extension, manifest, id: extensionId(manifest) };
}
module.exports = { STORE_ID, fail, inside, stateRoot, browserRoots, readManifest, extensionId,
  discoverExtension, prepareHome, prepareExtension };
