"""Tagless tracks remain named and usable in the actual browser scripts."""

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
const nodes = new Map(), cases = [];
const node = selector => {
  if (!nodes.has(selector)) nodes.set(selector, {
    content:'token', hidden:false, open:false, dataset:{}, innerHTML:'', textContent:'',
    addEventListener() {}, setAttribute(name, value) { this[name] = value; },
    getAttribute(name) { return this[name]; }, showModal() { this.open = true; },
    classList: { add() {}, remove() {}, toggle() {} }, play:async () => {},
  });
  return nodes.get(selector);
};
const context = {
  document: {querySelector:node, querySelectorAll:()=>[], addEventListener() {}, documentElement:{}},
  window: {addEventListener() {}}, navigator: {platform:'Linux'},
  localStorage: {getItem:()=>null}, tr:text=>text, uiLocale:()=>'en-US',
  URLSearchParams, protectionControl:()=>'',
};
vm.createContext(context);
for (const file of ['app.js', 'pages.js', 'player.js', 'advanced_downloads.js', 'manual.js'])
  vm.runInContext(fs.readFileSync(path.join(process.argv[1], 'src/acervo/web/static', file), 'utf8'), context);
const run = code => vm.runInContext(code, context);
context.track = {id:'bare', file:'/music/Faixa & <demo>.mp3', title:[], artist:[], album:[],
  year:[], genre:[], covers:[], protected_fields:[], format:'MP3', duration_seconds:30};
context.windowsFile = String.raw`C:\Music\Windows.flac`;
context.api = async () => context.track;
async function test(name, fn) {
  try { await fn(); cases.push({name, passed:true}); }
  catch (error) { cases.push({name, passed:false, error:error.stack}); }
}
(async () => {
  await test('empty and whitespace tags use a filename without altering metadata', () => {
    assert.equal(run('trackTitle(track)'), 'Faixa & <demo>.mp3');
    assert.equal(run('trackTitle({...track,title:[" ", ""]})'), 'Faixa & <demo>.mp3');
    assert.equal(run('trackTitle({...track,file:windowsFile})'), 'Windows.flac');
    assert.equal(run('trackTitle({...track,title:["Actual title"]})'), 'Actual title');
    assert.deepEqual(context.track.title, []);
  });
  await test('details use a placeholder, escaped filename and manual controls', async () => {
    await run('showDetails(track.id)');
    const html = node('#modal-content').innerHTML;
    assert.match(html, /detail-cover cover-placeholder/);
    assert.match(html, /Faixa &amp; &lt;demo&gt;\.mp3/);
    assert.match(html, /Unknown artist/);
    assert.match(html, /data-manual="edit"/);
    assert.match(html, /data-manual="cover"/);
    assert.ok(!html.includes('<img'));
    assert.match(html, /<dt>Title<\/dt><dd>Not set/);
  });
  await test('queue and now playing show names, artists and missing artwork', () => {
    run('state.current=track; setQueue([track,{...track,id:"next",has_cover:false}],track.id); renderNowView()');
    assert.equal(node('#now-title').textContent, 'Faixa & <demo>.mp3');
    assert.match(node('#now-artist').innerHTML, /Unknown artist/);
    assert.match(node('#now-cover').innerHTML, /cover-placeholder/);
    assert.equal(node('#now-backdrop').hidden, true);
    assert.match(node('#now-queue').innerHTML, /Faixa &amp; &lt;demo&gt;\.mp3/);
    assert.match(node('#now-queue').innerHTML, /cover-placeholder/);
    assert.ok(!node('#now-queue').innerHTML.includes('<img'));
    assert.match(context.document.title, /Faixa & <demo>\.mp3/);
  });
  await test('known covers in the queue remain images', () => {
    run('setQueue([track,{...track,id:"next",covers:undefined,has_cover:true}],track.id);renderQueue()');
    assert.match(node('#now-queue').innerHTML, /<img[^>]*\/api\/cover\?id=next/);
  });
  await test('album cards never have empty names or artist links', () => {
    run('state.summary={};');
    const html = run('albumCard({album:"",artist:"",track_id:"bare",has_cover:false,year:"",tracks:1})');
    assert.match(html, /Unknown album/); assert.match(html, /Unknown artist/);
    assert.ok(!html.includes('data-page-artist=""'));
    assert.match(run('artistLinks([" ",""])'), /Unknown artist/);
  });
  await test('staged playback uses the filename when tags are missing', async () => {
    run('downloadState.batchId="batch"; downloadState.batch={tracks:[{id:"preview",real:track}]}');
    await run('playStaged("preview")');
    assert.equal(node('#player-title').textContent, 'Faixa & <demo>.mp3');
    assert.match(node('#player-artist').textContent, /Unknown artist/);
  });
  await test('tag comparison labels both values and keeps complete escaped titles', () => {
    const html = run('tagComparison([{field:"title",current:["Curse of the Legions of Death"],proposed:["C.O.T.L.O.D."]},{field:"artist",current:[],proposed:["<script> & Artist"]}])');
    assert.match(html, /<th scope="col">Current value<\/th>/);
    assert.match(html, /<th scope="col">New value<\/th>/);
    assert.match(html, /Curse of the Legions of Death/);
    assert.match(html, /tag-changed/);
    assert.match(html, /&lt;script&gt; &amp; Artist/);
    assert.ok(!html.includes('<script>'));
  });
  await test('download comparison shares the same labelled columns', () => {
    const html = run('diffHTML({genre:{current:["Rock"],proposed:["Thrash Metal"]}})');
    assert.match(html, /tag-comparison/);assert.match(html, /Current value/);
    assert.match(html, /Rock/);assert.match(html, /Thrash Metal/);
  });
  await test('artwork review shows current and the exact candidate separately', () => {
    const html = run('coverComparison({...track,covers:[{type:3}]},"data:image/png;base64,QUFB")');
    assert.match(html, /Current cover/);assert.match(html, /New cover/);
    assert.match(html, /\/api\/cover\?id=bare/);
    assert.match(html, /data:image\/png;base64,QUFB/);
    const empty = run('coverComparison(track,"",true)');
    assert.match(empty, /id="manual-image-preview"/);
    assert.match(empty, /id="manual-cover-empty"/);
  });
  process.stdout.write(JSON.stringify(cases));
})().catch(error => {console.error(error); process.exitCode=1;});
"""


@unittest.skipUnless(shutil.which("node"), "JavaScript regressions need Node.js")
class FrontendDisplayTest(unittest.TestCase):
    def test_tagless_display_across_views(self):
        result = subprocess.run(
            [shutil.which("node"), "-e", SCRIPT, str(Path(__file__).resolve().parent.parent)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        cases = json.loads(result.stdout)
        self.assertEqual(len(cases), 9)
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertTrue(case["passed"], case.get("error"))
