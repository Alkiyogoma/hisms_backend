// Run with: node --test attendance/test_pwa_scanner.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const template = fs.readFileSync(path.join(__dirname, '../templates/attendance/pwa_app.html'), 'utf8');
const section = template.slice(template.indexOf('  // ── QR Scanner'), template.indexOf('  // ── Submit All'));
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function setup(start) {
  const elements = new Map();
  function element(key) {
    if (!elements.has(key)) {
      const classes = new Set();
      elements.set(key, {style:{}, textContent:'', value:'', classList:{
        add:x=>classes.add(x), remove:x=>classes.delete(x), contains:x=>classes.has(x)
      }});
    }
    return elements.get(key);
  }
  const requests = [], timers = new Map(), saved = [], notices = [];
  let timerId = 0;
  class Scanner {
    constructor(id, config) { this.config = config; }
    start(camera, config, success) {
      this.camera = camera; this.options = config; this.decode = success;
      return start ? start() : Promise.resolve();
    }
    pause(video) { this.pausedVideo = video; }
    resume() { this.resumed = true; }
    stop() { this.stopped = true; return Promise.resolve(); }
  }
  const context = vm.createContext({
    console, Promise, AbortController, Html5Qrcode:Scanner, Html5QrcodeSupportedFormats:{QR_CODE:0},
    html5Qr:null, isScanning:false, pendingStudentId:'', pendingSource:'qr',
    fb:{scanSuccess(){}, tap(){}},
    document:{getElementById:element, querySelector:element},
    setTimeout(fn) { const id = ++timerId; timers.set(id,fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    showToast(...args) { notices.push(args); },
    addPending(...args) { saved.push(args); },
    apiPost(url, body, signal) {
      return new Promise((resolve,reject) => {
        requests.push({body,signal,resolve,reject});
        signal.addEventListener('abort',()=>reject(Object.assign(new Error('Aborted'),{name:'AbortError'})));
      });
    }
  });
  context.window = context;
  vm.runInContext(section,context);
  return {context, element, requests, timers, saved, notices};
}

test('QR-only decoding uses native geometry, adaptive frame, and keeps video playing', async () => {
  const {context:c, element, requests, saved} = setup();
  c.openScanner();
  await flush();
  assert.deepEqual(Array.from(c.html5Qr.config.formatsToSupport),[0]);
  assert.equal(c.html5Qr.config.useBarCodeDetectorIfSupported,true);
  assert.equal(c.html5Qr.options.disableFlip,true);
  assert.equal(c.html5Qr.options.aspectRatio,undefined);
  const box = c.html5Qr.options.qrbox(320,240);
  assert.equal(box.width,180);
  assert.equal(element('.scan-frame').style.width,'180px');
  c.html5Qr.decode('  student-one  ');
  assert.notEqual(c.html5Qr.pausedVideo,true);
  assert.equal(requests[0].body.qr_code,'student-one');
  assert.equal(element('.btn-checkin').disabled,true);
  requests[0].resolve({data:{id:'1',name:'One'}});
  await flush();
  c.confirmMode('IN');
  assert.equal(saved[0][0],'1');
  assert.equal(saved[0][3],'qr');
  assert.equal(c.html5Qr.resumed,true);
});

test('slow lookup times out and cannot save unverified attendance', async () => {
  const {context:c, timers, requests, saved, element, notices} = setup();
  c.showStudentConfirm('one');
  for (const fn of timers.values()) fn();
  await flush();
  assert.equal(requests[0].signal.aborted,true);
  assert.equal(element('confirmName').textContent,'Lookup timed out');
  assert.match(notices[0][0],/connection/);
  c.confirmMode('IN');
  assert.equal(saved.length,0);
  assert.equal(timers.size,0);
});

test('cancelled lookup cannot replace a newer student or unlock confirmation', async () => {
  const {context:c, requests, element} = setup();
  c.showStudentConfirm('one');
  // Simulate a response already resolved immediately before cancellation.
  requests[0].resolve({data:{id:'1',name:'Old'}});
  c.cancelConfirm();
  c.showStudentConfirm('two');
  await flush();
  assert.equal(element('confirmName').textContent,'Looking up student...');
  assert.equal(element('.btn-checkin').disabled,true);
  requests[1].resolve({data:{id:'2',name:'New'}});
  await flush();
  assert.equal(element('confirmName').textContent,'New');
  assert.equal(element('.btn-checkin').disabled,false);
});

test('manual lookup resets prior failure and preserves manual source', async () => {
  const {context:c, requests, element, saved} = setup();
  c.showStudentConfirm('bad');
  requests[0].reject(new Error('Not found'));
  await flush();
  c.cancelConfirm();
  element('manualStudentId').value = 'two';
  c.submitManual();
  requests[1].resolve({data:{id:'2',name:'Two'}});
  await flush();
  c.confirmMode('OUT');
  assert.equal(saved[0][3],'manual');
});

test('closing during camera startup waits for startup before releasing the camera', async () => {
  let resolveStart;
  const {context:c} = setup(()=>new Promise(resolve=>{resolveStart=resolve}));
  c.openScanner();
  c.closeScanner();
  assert.equal(c.html5Qr.stopped,undefined);
  resolveStart();
  await flush();
  assert.equal(c.html5Qr.stopped,true);
  assert.equal(c.isScanning,false);
});

test('all inline scripts parse', () => {
  for (const match of template.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) {
    new vm.Script(match[1]);
  }
});
