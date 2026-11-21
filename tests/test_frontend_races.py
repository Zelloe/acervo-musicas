"""Exercise asynchronous UI decisions against the actual scripts, without network or music."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const staticRoot = path.join(process.argv[1], 'src/acervo/web/static');
const cases = [];
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function environment(player = false) {
  const nodes = new Map();
  const listeners = {};
  const audioListeners = {};
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, {
      open: true, hidden: true, dataset: {}, firstElementChild: {},
      setAttribute() {}, addEventListener() {}, focus() {},
      classList: { add() {}, remove() {}, toggle() {} },
    });
    return nodes.get(selector);
  };
  const context = {
    state: { view: 'library', current: null, queue: [] },
    document: {
      documentElement: {}, querySelectorAll: () => [],
      addEventListener: (kind, fn) => (listeners[kind] ??= []).push(fn),
    },
    window: { addEventListener() {} }, navigator: { platform: 'Linux' },
    localStorage: { getItem: () => null },
    audio: { addEventListener: (kind, fn) => audioListeners[kind] = fn, pause() {}, load() {}, removeAttribute() {} },
    $: node, tr: value => value, escapeHTML: value => String(value ?? ''),
    join: values => (values || []).join(' / '), number: String, bytes: String,
    icon: () => '', modalHead: (title, text) => title + text, trackTitle: track => track.title?.[0] || 'Untitled',
    trackArtist: track => track.artist?.[0] || '', trackArtwork: () => '',
    duration: String, artistLinks: () => '', albumLink: () => '',
    setWriteControls() {}, updateState: async () => {}, setLyricsTrack() {}, closeLyrics() {},
    toasts: [], html: '',
  };
  context.toast = text => context.toasts.push(text);
  context.modal = html => {
    context.html = html;
    node('#modal-content').firstElementChild = {};
    node('#modal').open = true;
  };
  node('#modal').close = () => { node('#modal').open = false; };
  vm.createContext(context);
  if (player) vm.runInContext(fs.readFileSync(path.join(staticRoot, 'player.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(staticRoot, 'manual.js'), 'utf8'), context);
  return { context, node, listeners, audioListeners, run: code => vm.runInContext(code, context) };
}
function coverRequest(e, pending) {
  const submit = { disabled: false };
  const form = { id: 'manual-cover-form', dataset: {}, isConnected: false, querySelector: () => submit };
  e.run("manualState.track = {id:'a'}; manualState.image='data:image/png;base64,QUFB';");
  e.context.api = () => pending.promise;
  const event = { target: form, preventDefault() {} };
  return { form, event, done: e.listeners.submit[0](event) };
}
const preview = { preview:'token-a', kind:'cover', count:1, skipped:0, size_bytes:10, sample:[] };
async function test(name, fn) {
  try { await fn(); cases.push({name, passed:true}); }
  catch (error) { cases.push({name, passed:false, error:error.stack}); }
}
(async () => {
  await test('cover preview keeps the image actually submitted', async () => {
    const e = environment(), pending = deferred();
    const {done} = coverRequest(e, pending);
    e.run("manualState.image='data:image/png;base64,QkJC';");
    pending.resolve(preview); await done;
    assert.ok(e.context.html.includes('data:image/png;base64,QUFB'));
    assert.ok(!e.context.html.includes('data:image/png;base64,QkJC'));
  });
  await test('cancelled preview cannot reopen its dialog', async () => {
    const e = environment(), pending = deferred();
    const {done} = coverRequest(e, pending);
    e.node('#modal').open = false;
    pending.resolve(preview); await done;
    assert.equal(e.context.html, ''); assert.equal(e.node('#modal').open, false);
  });
  await test('older preview cannot replace a newer dialog', async () => {
    const e = environment(), pending = deferred();
    const {done} = coverRequest(e, pending);
    e.context.modal('Track details'); pending.resolve(preview); await done;
    assert.equal(e.context.html, 'Track details');
  });
  await test('navigation invalidates an outstanding manual request', async () => {
    const e = environment(), pending = deferred();
    const {done} = coverRequest(e, pending);
    e.context.state.view = 'history'; pending.resolve(preview); await done;
    assert.equal(e.context.html, '');
  });
  await test('stale errors do not interrupt a newer dialog', async () => {
    const e = environment(), pending = deferred();
    const {done} = coverRequest(e, pending);
    e.context.modal('Track details'); pending.reject(new Error('expired')); await done;
    assert.equal(e.context.html, 'Track details'); assert.equal(e.context.toasts.length, 0);
  });
  await test('a form cannot submit a second preview while pending', async () => {
    const e = environment(), pending = deferred();
    const {event, done} = coverRequest(e, pending);
    let extra = 0; e.context.api = async () => { extra++; return preview; };
    await e.listeners.submit[0](event); pending.resolve(preview); await done;
    assert.equal(extra, 0);
  });
  await test('latest manual request wins', async () => {
    const e = environment(), first = deferred(), second = deferred();
    e.context.api = () => first.promise; const a = e.run("showManualEditor('first')");
    e.context.api = () => second.promise; const b = e.run("showManualEditor('second')");
    second.resolve({id:'second', file:'/music/second.mp3', fields:{}, protected_fields:[]}); await b;
    first.resolve({id:'first', file:'/music/first.mp3', fields:{}, protected_fields:[]}); await a;
    assert.equal(e.run('manualState.editor.id'), 'second');
    assert.ok(e.context.html.includes('second.mp3')); assert.ok(!e.context.html.includes('first.mp3'));
  });
  await test('a save response cannot close or clear a newer editor', async () => {
    const e = environment(), pending = deferred();
    e.run("manualState.preview={preview:'original'}"); e.context.api = () => pending.promise;
    const button = { dataset:{manual:'save'}, disabled:false };
    const target = { closest: selector => selector === '[data-manual]' ? button : null };
    const done = e.listeners.click[0]({target});
    e.context.modal('New editor'); e.run("manualState.preview={preview:'new'}; manualState.image='new image';");
    pending.resolve({accepted:true}); await done;
    assert.equal(e.context.html, 'New editor'); assert.equal(e.node('#modal').open, true);
    assert.equal(e.run('manualState.preview.preview'), 'new'); assert.equal(e.run('manualState.image'), 'new image');
  });
  await test('library refresh cannot stop a newer track absent from its old snapshot', async () => {
    const e = environment(true), pending = deferred();
    e.run("state.current={id:'a'}; setQueue([{id:'a'}],'a');"); e.context.api = () => pending.promise;
    const done = e.run('refreshManualPlayback()');
    e.run("++playback.serial; state.current={id:'new'}; setQueue([{id:'new'}],'new');");
    pending.resolve({tracks:[{id:'a'}]}); await done;
    assert.equal(e.run('state.current.id'), 'new'); assert.equal(e.run('playback.base[0]'), 'new');
  });
  await test('new queue survives refresh waiting for track metadata', async () => {
    const e = environment(true), pending = deferred();
    e.run("state.current={id:'a'}; setQueue([{id:'a'}],'a');");
    let requested; const gate = deferred();
    e.context.api = route => {
      if (route === '/api/queue') return Promise.resolve({tracks:[{id:'a'}]});
      requested = true; gate.resolve(); return pending.promise;
    };
    const done = e.run('refreshManualPlayback()'); await gate.promise; assert.ok(requested);
    e.run("++playback.serial; state.current={id:'b'}; setQueue([{id:'b'}],'b');");
    pending.resolve({id:'a'}); await done;
    assert.equal(e.run('state.current.id'), 'b'); assert.equal(e.run('playback.base[0]'), 'b');
  });
  await test('a library track queued after a staged preview is reachable', async () => {
    const e = environment(true);
    e.run("state.current={id:'stage-a'}; setQueue([{id:'b'}],'stage-a');");
    assert.equal(e.run('neighbour(1)'), 'b'); assert.equal(e.run('neighbour(-1)'), null);
    assert.equal(e.run('upcomingTracks().join()'), 'b');
  });
  await test('an empty player does not expose an unstarted queue', async () => {
    const e = environment(true); e.run("setQueue([{id:'b'}],null)");
    assert.equal(e.run('neighbour(1)'), null); assert.equal(e.run('upcomingTracks().length'), 0);
  });
  process.stdout.write(JSON.stringify(cases));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "JavaScript regressions need Node.js")
class FrontendRacesTest(unittest.TestCase):
    def test_manual_previews_and_playback_ignore_stale_responses(self):
        result = subprocess.run(
            [shutil.which("node"), "-e", SCRIPT, str(Path(__file__).resolve().parent.parent)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        cases = json.loads(result.stdout)
        self.assertEqual(len(cases), 12)
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertTrue(case["passed"], case.get("error"))
