const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { findPlaywright } = require('../skills/pod-image-selection/scripts/environment.cjs');
const { prepareHome } = require('../skills/pod-image-selection/scripts/sellersprite_session.cjs');
const { authorize } = require('../skills/pod-image-selection/scripts/sellersprite_auth.cjs');

test('authorization persists a private HTTP session using only SellerSprite pages and normal extension messages', { timeout: 60000 }, async () => {
  const runtime = findPlaywright();
  assert.ok(runtime.playwright);
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'pod-http-auth-test-'));
  const ext = path.join(tmp, 'fixture');
  await fs.mkdir(ext);
  const key = crypto.generateKeyPairSync('rsa', { modulusLength: 1024 }).publicKey.export({ type: 'spki', format: 'der' }).toString('base64');
  await fs.writeFile(path.join(ext, 'manifest.json'), JSON.stringify({ manifest_version: 3, name: 'Authorization fixture', version: '1.0.0', key,
    permissions: ['storage'], host_permissions: ['https://e.sellersprite.com/*'], action: { default_popup: 'popup.html' },
    background: { service_worker: 'worker.js' }, content_scripts: [{ matches: ['https://www.sellersprite.com/*'], js: ['web.js'] }] }));
  await fs.writeFile(path.join(ext, 'popup.html'), '<title>Authorization fixture</title>');
  await fs.writeFile(path.join(ext, 'web.js'), 'const b=document.querySelector("button");b.onclick=()=>chrome.storage.local.set({webToken:"fixture-web-token"});b.disabled=false;');
  await fs.writeFile(path.join(ext, 'worker.js'), `
    chrome.runtime.onMessage.addListener((message,sender,respond)=>{
      (async()=>{
        const state=await chrome.storage.local.get(['webToken','signedIn']);
        if(message.action==='AUTO_SIGN_IN_INFO')return {code:'OK',data:state.webToken?{token:state.webToken}:null};
        if(message.action==='SIGN_IN_WITH_TOKEN'){
          await chrome.storage.local.set({__FP:{value:'fixture-fp',expire:Date.now()+3600000},__UUID:'fixture-uuid'});
          setTimeout(()=>chrome.storage.local.set({signedIn:true}),150);
          return {code:'OK',data:{}};
        }
        if(message.action==='CLIENT_LATEST'){
          if(!state.signedIn)return {code:'OK',data:null};
          return {code:'OK',data:{token:'fixture-new-token',nickname:'not-saved'}};
        }
        return {code:'ERROR'};
      })().then(respond);return true;
    });
  `);
  let current, home;
  const requests = [], events = [];
  const dependencies = { prepare: async () => ({ report: { ready: true }, playwright: runtime.playwright }),
    desktop: () => ({ available: true, reason: 'test_fixture' }),
    launch: async (profile, options) => {
      current = await runtime.playwright.chromium.launchPersistentContext(profile, { ...options, headless: true });
      current.on('request', request => requests.push(request.url()));
      await current.route('**/*', route => route.abort());
      await current.route('https://www.sellersprite.com/**', route => route.fulfill({ contentType: 'text/html',
        body: '<!doctype html><title>Website login fixture</title><button disabled>Fixture login</button>' }));
      await current.route('https://e.sellersprite.com/**', route => route.fulfill({ contentType: 'application/json',
        body: JSON.stringify({code:'OK',data:{token:'fixture-new-token',nickname:'not-saved'}}) }));
      return current;
    }
  };
  const publish = async value => {
    events.push(value);
    if(value === 'awaiting_login') {
      await current.pages().find(p=>p.url().startsWith('https://www.sellersprite.com/'))
        .getByRole('button',{name:'Fixture login'}).click({timeout:2000});
    }
  };
  try {
    home = await prepareHome(path.join(tmp,'state'),path.join(tmp,'out'));
    const options = { out: path.join(tmp,'out'), timeout: 5, 'extension-dir': ext };
    const sessionPath = await authorize(home,options,publish,dependencies);
    const session = JSON.parse(await fs.readFile(sessionPath));
    assert.equal(session.headers['auth-token'],'fixture-new-token');
    assert.ok(!JSON.stringify(session).includes('not-saved'));
    if(process.platform !== 'win32')assert.equal((await fs.stat(sessionPath)).mode & 0o777,0o600);
    assert.ok(events.includes('awaiting_login'));
    events.length=0;
    await authorize(home,options,publish,dependencies);
    assert.deepEqual(events,['reused']);
    for (const [name, code] of [['cancel','AUTHORIZATION_CANCELLED'],['timeout','AUTHORIZATION_TIMEOUT'],['remote','INTERACTIVE_BROWSER_REQUIRED']]) {
      const fresh = await prepareHome(path.join(tmp,'state-'+name),path.join(tmp,'out-'+name));
      try {
        const notify = async status => { if(name==='cancel' && status==='awaiting_login') await current.close(); };
        const deps = name==='remote' ? {...dependencies,desktop:()=>({available:false,reason:'fixture_container'})} : dependencies;
        await assert.rejects(authorize(fresh,{...options,out:path.join(tmp,'out-'+name),timeout:0.1},notify,deps),{code});
      } finally {await fresh.release();}
    }
    assert.equal(requests.filter(url=>/^https:\/\/[^/]*amazon\./.test(url)).length,0);
    process.stdout.write(JSON.stringify({privateSessionSaved:true,persistentReuse:true,amazonRequests:0,realAccountVerified:false})+'\n');
  } finally {await current?.close().catch(()=>{});await home?.release();await fs.rm(tmp,{recursive:true,force:true,maxRetries:5,retryDelay:100});}
});
