"""Filename suggestions fill missing tags and keep the reviewed save flow."""

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
const cases = [];
function environment() {
  const nodes = new Map(), listeners = {}, inputs = {};
  for (const name of ['title','artist','album_artist','album','genre','year','track','disc'])
    inputs[name] = {value:'', disabled:false};
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {dataset:{}, open:true, firstElementChild:{}});
    return nodes.get(selector);
  };
  const form = node('#manual-edit-form');
  form.id = 'manual-edit-form';
  form.elements = {namedItem:name=>inputs[name]};
  form.querySelector = () => ({disabled:false});
  form.querySelectorAll = () => [];
  form.isConnected = true;
  const context = {
    $:node, state:{view:'library'}, document:{addEventListener:(kind,fn)=>(listeners[kind]??=[]).push(fn)},
    window:{addEventListener() {}}, tr:text=>text, escapeHTML:value=>String(value??''),
    number:String, bytes:String, icon:()=>'', modalHead:(title,detail)=>title+detail,
    trackTitle:track=>track.file, join:values=>(values||[]).join(' / '), setWriteControls() {},
    toasts:[], requests:[],
  };
  context.toast = value => context.toasts.push(value);
  context.modal = html => {context.html=html; node('#modal-content').firstElementChild={};};
  context.api = async (url,data) => {
    context.requests.push({url,data});
    return {kind:'edit',preview:'review-token',count:1,size_bytes:1,sample:[{file:'/music/source.mp3'}]};
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(process.argv[1], 'src/acervo/web/static/manual.js'), 'utf8'), context);
  const run = code => vm.runInContext(code, context);
  run('manualState.editor={id:"bare",file:"/music/02 - AC-DC - Live - Demo.mp3",fields:{},protected_fields:[],previous_sha256:"original-hash"}');
  const click = async () => {
    const button = {dataset:{manual:'filename'}, disabled:false};
    await listeners.click[0]({target:{closest:selector=>selector==='[data-manual]' ? button : null}});
  };
  return {context,run,node,inputs,form,listeners,click};
}
async function test(name, fn) {
  try {await fn();cases.push({name,passed:true});}
  catch(error) {cases.push({name,passed:false,error:error.stack});}
}
(async () => {
  for (const [file,expected] of [
    ['/music/01 - AC-DC - T.N.T..MP3',{track:'1',artist:'AC-DC',title:'T.N.T.'}],
    ['AC-DC - Live - Demo.flac',{artist:'AC-DC',title:'Live - Demo'}],
    ['02. Song.mp3',{track:'2',title:'Song'}],
    ['7) Song.mp3',{track:'7',title:'Song'}],
    ['003 Song.mp3',{track:'3',title:'Song'}],
    ['3 Doors Down - Kryptonite.mp3',{artist:'3 Doors Down',title:'Kryptonite'}],
    ['10 Years - Wasteland.mp3',{artist:'10 Years',title:'Wasteland'}],
    ['Banda – Canção — Ao vivo.flac',{artist:'Banda',title:'Canção — Ao vivo'}],
    [String.raw`C:\Music\04 - Banda - Canção.flac`,{track:'4',artist:'Banda',title:'Canção'}],
    ['123.mp3',{title:'123'}],
    ['Banda_Canção.mp3',{title:'Banda_Canção'}],
    ['Song (Live 1991).mp3',{title:'Song (Live 1991)'}],
    ['  Song\nwith spaces  .mp3',{title:'Song with spaces'}],
    ['.mp3',{}],
  ]) await test('parse '+file, () => {
    const e=environment();e.context.file=file;
    assert.deepEqual(JSON.parse(JSON.stringify(e.run('tagsFromFilename(file)'))),expected);
  });
  await test('click fills missing fields without changing metadata or calling the server', async () => {
    const e=environment(), before=e.run('JSON.stringify(manualState.editor)');
    await e.click();
    assert.equal(e.inputs.title.value,'Live - Demo');
    assert.equal(e.inputs.artist.value,'AC-DC');
    assert.equal(e.inputs.track.value,'2');
    assert.equal(e.inputs.album.value,'');assert.equal(e.inputs.genre.value,'');
    assert.equal(e.run('JSON.stringify(manualState.editor)'),before);
    assert.equal(e.context.requests.length,0);
  });
  await test('existing tags, typed values and protected empty fields are preserved', async () => {
    const e=environment();
    e.run('manualState.editor.fields={title:["Existing title"]};manualState.editor.protected_fields=["track"]');
    e.inputs.title.value='';e.inputs.artist.value='My manual artist';
    await e.click();
    assert.equal(e.inputs.title.value,'');assert.equal(e.inputs.artist.value,'My manual artist');
    assert.equal(e.inputs.track.value,'');assert.equal(e.context.requests.length,0);
  });
  await test('a second click keeps edits to the first suggestions', async () => {
    const e=environment();await e.click();e.inputs.title.value='Corrected title';await e.click();
    assert.equal(e.inputs.title.value,'Corrected title');assert.equal(e.inputs.artist.value,'AC-DC');
    assert.equal(e.context.requests.length,0);
  });
  await test('pending previews and disabled fields cannot be changed by suggestions', async () => {
    const e=environment();e.form.dataset.manualPending='true';await e.click();
    assert.equal(e.inputs.title.value,'');assert.equal(e.context.toasts.length,0);
    delete e.form.dataset.manualPending;e.inputs.title.disabled=true;await e.click();
    assert.equal(e.inputs.title.value,'');assert.equal(e.inputs.artist.value,'AC-DC');
  });
  await test('suggested values go through preview and require a separate save', async () => {
    const e=environment();await e.click();
    await e.listeners.submit[0]({target:e.form,preventDefault() {}});
    assert.equal(e.context.requests.length,1);
    const request=JSON.parse(JSON.stringify(e.context.requests[0]));
    assert.equal(request.url,'/api/tracks/edit/preview');
    assert.deepEqual(request.data,{id:'bare',fields:{title:['Live - Demo'],artist:['AC-DC'],track:['2']},previous_sha256:'original-hash'});
    assert.match(e.context.html,/Review tag changes/);
    assert.match(e.context.html,/data-manual="save"/);
  });
  await test('editor exposes the filename action as a button separate from submit', async () => {
    const e=environment();e.context.api=async()=>e.run('manualState.editor');
    await e.run('showManualEditor("bare")');
    assert.match(e.context.html,/<button type="button"[^>]*data-manual="filename"/);
    assert.match(e.context.html,/Only missing, unprotected tags are filled/);
  });
  process.stdout.write(JSON.stringify(cases));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


@unittest.skipUnless(shutil.which("node"), "JavaScript regressions need Node.js")
class FilenameTagsTest(unittest.TestCase):
    def test_filename_suggestions_preserve_choices_and_require_review(self):
        result = subprocess.run(
            [shutil.which("node"), "-e", SCRIPT, str(Path(__file__).resolve().parent.parent)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        cases = json.loads(result.stdout)
        self.assertEqual(len(cases), 20)
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertTrue(case["passed"], case.get("error"))
