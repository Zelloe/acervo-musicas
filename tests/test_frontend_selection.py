"""Selection scope and asynchronous editor responses stay tied to the user's choices."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const cases=[];
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};}
function environment(){
  const bar={hidden:true,innerHTML:''},header={},messages=[],shown=[];
  const e={boxes:[]};
  const context={
    state:{view:'library',summary:{library:'/music',library_revision:'v1'}},
    document:{addEventListener(){},querySelectorAll:selector=>selector==='#content [data-track-select]'?e.boxes:selector==='[data-track-select-visible]'?[header]:[]},
    $:()=>bar,tr:s=>s,number:String,icon:()=>'',setWriteControls(){},
    escapeHTML:s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]),
    trackTitle:track=>track.title?.[0]||track.file,
    api:async()=>({tracks:[]}),manualRead:async()=>null,
    toast:s=>messages.push(s),showCommonEditForm:editor=>shown.push(editor),
  };
  vm.createContext(context);
  const run=code=>vm.runInContext(code,context);
  run(fs.readFileSync(path.join(process.argv[1],'src/acervo/web/static/track_selection.js'),'utf8'));
  function boxes(ids){e.boxes=ids.map(id=>({dataset:{trackSelect:id},checked:false,closest:()=>({classList:{toggle(){}}})}));}
  run('refreshTrackSelection()');
  return {...e,context,run,bar,header,messages,shown,boxes,get checks(){return e.boxes;}};
}
async function test(name,fn){try{await fn();cases.push({name,passed:true});}catch(error){cases.push({name,passed:false,error:error.stack});}}
(async()=>{
 await test('selection survives a different filter and explicitly counts hidden tracks',()=>{
  const e=environment();e.boxes(['a','b']);e.run("changeTrackSelection(['a','b'],true)");
  assert.equal(e.header.checked,true);e.boxes(['b']);e.run('renderTrackSelection()');
  assert.match(e.bar.innerHTML,/Selected tracks: 2/);assert.match(e.bar.innerHTML,/1 outside this view/);
  assert.equal(e.checks[0].checked,true);e.boxes([]);e.run('renderTrackSelection()');assert.equal(e.bar.hidden,false);
 });
 await test('newly loaded tracks are not silently selected and master state becomes partial',()=>{
  const e=environment();e.boxes(['a']);e.run("changeTrackSelection(['a'],true)");
  e.boxes(['a','b']);e.run('renderTrackSelection()');
  assert.equal(e.header.checked,false);assert.equal(e.header.indeterminate,true);assert.equal(e.checks[1].checked,false);
 });
 await test('selection limit is atomic and clear removes the entire selection',()=>{
  const e=environment();e.run("changeTrackSelection(Array.from({length:499},(_,i)=>String(i)),true)");
  e.run("changeTrackSelection(['new-a','new-b'],true)");assert.equal(e.run('trackSelection.ids.size'),499);
  assert.equal(e.messages.length,1);assert.equal(e.run("trackSelection.ids.has('new-a')"),false);
  e.run('changeTrackSelection([...trackSelection.ids],false)');assert.equal(e.bar.hidden,true);
 });
 await test('changing library clears selection rather than carrying ids to a new root',()=>{
  const e=environment();e.run("changeTrackSelection(['a'],true)");
  e.context.state.summary={library:'/other',library_revision:'v2'};e.run('refreshTrackSelection()');
  assert.equal(e.run('trackSelection.ids.size'),0);assert.equal(e.run('trackSelection.library'),'/other');
 });
 await test('revision pruning removes deleted tracks and keeps newer selections',async()=>{
  const e=environment(),pending=deferred();e.run("changeTrackSelection(['deleted','kept'],true)");
  e.context.api=()=>pending.promise;e.context.state.summary.library_revision='v2';e.run('refreshTrackSelection()');
  e.run("changeTrackSelection(['new'],true)");pending.resolve({tracks:[{id:'kept'}]});
  await new Promise(setImmediate);assert.deepEqual(Array.from(e.run('[...trackSelection.ids]')),['kept','new']);
 });
 await test('a stale queue response cannot erase a new library selection',async()=>{
  const e=environment(),pending=deferred();e.run("changeTrackSelection(['old'],true)");
  e.context.api=()=>pending.promise;e.context.state.summary.library_revision='v2';e.run('refreshTrackSelection()');
  e.context.state.summary={library:'/other',library_revision:'v3'};e.run("refreshTrackSelection();changeTrackSelection(['new'],true)");
  pending.resolve({tracks:[]});await new Promise(setImmediate);assert.deepEqual(Array.from(e.run('[...trackSelection.ids]')),['new']);
 });
 await test('changing the selection while an editor loads discards that editor',async()=>{
  const e=environment(),pending=deferred();e.run("changeTrackSelection(['a'],true)");e.context.manualRead=()=>pending.promise;
  const opening=e.run('editSelectedTracks()');e.run("changeTrackSelection(['b'],true)");
  pending.resolve({ids:['a'],kind:'batch-edit'});await opening;assert.equal(e.shown.length,0);
 });
 await test('duplicate editor opening is blocked and successful request uses captured ids',async()=>{
  const e=environment(),pending=deferred();e.run("changeTrackSelection(['a','b'],true)");let count=0,payload;
  e.context.manualRead=(route,data)=>{count++;payload=data;return pending.promise;};
  const first=e.run('editSelectedTracks()');await e.run('editSelectedTracks()');assert.equal(count,1);
  pending.resolve({ids:['a','b'],kind:'batch-edit'});await first;
  assert.deepEqual(Array.from(payload.ids),['a','b']);assert.equal(e.shown.length,1);
 });
 await test('clear invalidates a pending response and avoids stale error messages',async()=>{
  const e=environment();e.run("changeTrackSelection(['a'],true)");let reject;
  e.context.manualRead=()=>new Promise((resolve,no)=>reject=no);
  const opening=e.run('editSelectedTracks()');e.run("changeTrackSelection(['a'],false)");
  reject(new Error('old error'));await opening;assert.equal(e.shown.length,0);assert.equal(e.messages.length,0);
 });
 process.stdout.write(JSON.stringify(cases));process.exitCode=cases.every(item=>item.passed)?0:1;
})();
"""


@unittest.skipUnless(shutil.which("node"), "JavaScript checks require Node")
class FrontendSelectionTest(unittest.TestCase):
    def test_scope_and_asynchronous_responses(self):
        result = subprocess.run(
            ["node", "-e", SCRIPT, str(Path(__file__).resolve().parent.parent)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertTrue(result.stdout, result.stderr)
        cases = json.loads(result.stdout)
        self.assertEqual(len(cases), 9)
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertTrue(case["passed"], case.get("error"))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
