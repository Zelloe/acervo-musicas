"""Exercise restoration and checkpoint races in the actual browser scripts."""

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
function deferred() {
  let resolve;
  const promise = new Promise(done => resolve = done);
  return {promise, resolve};
}
function environment() {
  const nodes = new Map(), events = {}, posts = [], messages = [];
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      hidden:true, dataset:{}, addEventListener(){}, setAttribute(){},
      classList:{toggle(){}}, querySelector:()=>({setAttribute(){}}),
    });
    return nodes.get(selector);
  };
  const tracks = ['a','b','c'].map(id => ({id,title:[id],artist:[],file:`/music/${id}.mp3`}));
  const session = {track_id:'b', position_seconds:12.5, queue:['a','b','c'], order:['c','b','a'],
    shuffle:true, repeat:'all', volume:.35, muted:true, queue_revision:4};
  const data = {library_path:'/music', enabled:true, ready:true, checkpoint_at:10, session};
  let timer = 0;
  const context = {
    $:node, tr:s=>s, toast:message=>messages.push(message),
    state:{summary:{library:'/music'},current:null},
    audio:{currentTime:0, readyState:0, volume:.7, muted:false, paused:true, playCalls:0,
      play(){this.playCalls++;}, addEventListener:(kind,fn)=>(events[kind]??=[]).push(fn)},
    document:{querySelectorAll:()=>[],addEventListener(){},documentElement:{}},
    window:{addEventListener(){}}, localStorage:{getItem:()=>null}, navigator:{platform:'Linux'},
    setTimeout:()=>++timer, clearTimeout(){}, duration:String, join:values=>(values||[]).join(' / '),
    trackTitle:t=>t.title[0],
    api:async (route, body, options) => {
      if(body) { posts.push({body,options}); return {enabled:true}; }
      if(route==='/api/playback/session') return data;
      if(route==='/api/queue') return {tracks};
      return tracks.find(t=>route.endsWith('='+t.id));
    },
  };
  vm.createContext(context);
  const run = code=>vm.runInContext(code,context);
  for(const file of ['player.js','playback_session.js'])
    run(fs.readFileSync(path.join(process.argv[1],'src/acervo/web/static',file),'utf8'));
  context.loadPlayerTrack = track => {context.state.current=track;context.audio.readyState=0;run('syncPlayback(state.current)');};
  return {context,run,node,events,posts,messages,data,tracks};
}
function active(e) {
  e.run("playbackSessionState.ready=true;playbackSessionState.loaded=true;playbackSessionState.library='/music';state.current={id:'b',title:['b']};setQueue([{id:'a'},{id:'b'},{id:'c'}],'b')");
}
async function test(name, fn) {
  try {await fn();cases.push({name,passed:true});}
  catch(error){cases.push({name,passed:false,error:error.stack});}
}
(async()=>{
  await test('restores an exact shuffled queue and waits for metadata without autoplay', async()=>{
    const e=environment();await e.run('initializePlaybackSession()');
    assert.equal(e.context.state.current.id,'b');
    assert.deepEqual(Array.from(e.run('playback.order')),['c','b','a']);
    assert.equal(e.run('playback.index'),1);assert.equal(e.run('playback.shuffle'),true);
    assert.equal(e.run('playback.repeat'),'all');assert.equal(e.context.audio.volume,.35);
    assert.equal(e.context.audio.muted,true);assert.equal(e.node('#volume').value,35);
    assert.equal(e.run('playbackCheckpoint()'),undefined);
    e.context.audio.readyState=1;e.context.audio.duration=30;
    for(const callback of e.events.loadedmetadata) callback();
    assert.equal(e.context.audio.currentTime,12.5);
    assert.equal(e.context.audio.playCalls,0);assert.equal(e.context.audio.paused,true);
    assert.equal(e.run('playbackCheckpoint().position_seconds'),12.5);
  });
  await test('a delayed saved session cannot replace a track the user just started', async()=>{
    const e=environment(),pending=deferred();e.context.api=()=>pending.promise;
    const loading=e.run('initializePlaybackSession()');
    e.run("++playback.serial;state.current={id:'new'}");
    pending.resolve(e.data);await loading;
    assert.equal(e.context.state.current.id,'new');assert.equal(e.context.audio.playCalls,0);
  });
  await test('a delayed track lookup cannot replace newer playback or queue', async()=>{
    const e=environment(),pending=deferred(),requested=deferred(),original=e.context.api;
    e.context.api=(route,...args)=>{
      if(route.startsWith('/api/track?')){requested.resolve();return pending.promise;}
      return original(route,...args);
    };
    const loading=e.run('initializePlaybackSession()');await requested.promise;
    e.run("++playback.serial;state.current={id:'a'};setQueue([{id:'a'}],'a')");
    pending.resolve(e.tracks[1]);await loading;
    assert.equal(e.context.state.current.id,'a');assert.deepEqual(Array.from(e.run('playback.base')),['a']);
  });
  await test('new playback invalidates a pending restored seek', async()=>{
    const e=environment();await e.run('initializePlaybackSession()');
    e.run("++playback.serial;state.current={id:'a'}");
    e.context.audio.readyState=1;e.context.audio.duration=30;e.context.audio.currentTime=2;
    for(const callback of e.events.loadedmetadata) callback();
    assert.equal(e.context.audio.currentTime,2);
  });
  await test('closing during a delayed restoration cannot clear the existing checkpoint', async()=>{
    const e=environment(),pending=deferred(),requested=deferred(),original=e.context.api;
    e.context.api=(route,...args)=>{
      if(route.startsWith('/api/track?')){requested.resolve();return pending.promise;}
      return original(route,...args);
    };
    const loading=e.run('initializePlaybackSession()');await requested.promise;
    await e.run('flushPlaybackSession(true)');assert.equal(e.posts.length,0);
    pending.resolve(e.tracks[1]);await loading;
  });
  await test('disabling while metadata is pending cancels restoration', async()=>{
    const e=environment(),pending=deferred(),requested=deferred(),original=e.context.api;
    e.context.api=(route,...args)=>{
      if(route.startsWith('/api/track?')){requested.resolve();return pending.promise;}
      return original(route,...args);
    };
    const loading=e.run('initializePlaybackSession()');await requested.promise;
    await e.run('setPlaybackSessionEnabled(false)');
    pending.resolve(e.tracks[1]);await loading;
    assert.equal(e.context.state.current,null);assert.equal(e.run('playbackSessionState.enabled'),false);
  });
  await test('an unfinished startup scan is retried without erasing the saved session', async()=>{
    const e=environment();e.data.ready=false;
    await e.run('initializePlaybackSession()');await e.run('flushPlaybackSession()');
    assert.equal(e.posts.length,0);assert.equal(e.context.state.current,null);
    e.data.ready=true;await e.run('initializePlaybackSession()');
    assert.equal(e.context.state.current.id,'b');
  });
  await test('unchanged checkpoints do not rewrite disk and staged previews keep the last library track', async()=>{
    const e=environment();active(e);e.context.audio.currentTime=9;
    await e.run('flushPlaybackSession()');await e.run('flushPlaybackSession()');
    assert.equal(e.posts.length,1);assert.equal(e.posts[0].body.session.position_seconds,9);
    e.run("state.current={id:'stage-preview'}");await e.run('flushPlaybackSession()');
    assert.equal(e.posts.length,1);
    e.run('state.current=null');await e.run('flushPlaybackSession()');
    assert.equal(e.posts[1].body.session,null);
  });
  await test('disabling clears saved state and prevents subsequent checkpoints', async()=>{
    const e=environment();active(e);await e.run('setPlaybackSessionEnabled(false)');
    assert.equal(e.posts[0].body.enabled,false);
    await e.run('flushPlaybackSession()');assert.equal(e.posts.length,1);
    await e.run('setPlaybackSessionEnabled(true)');
    assert.equal(e.posts[1].body.enabled,true);assert.equal(e.posts[2].body.session.track_id,'b');
  });
  await test('checkpoints remain bound to their original library and serialize ordinary writes', async()=>{
    const e=environment();active(e);const pending=deferred(),started=deferred(),original=e.context.api;
    let count=0;e.context.api=(route,body,options)=>{
      if(++count===1){e.posts.push({body,options});started.resolve();return pending.promise;}
      return original(route,body,options);
    };
    const first=e.run('flushPlaybackSession()');await started.promise;
    e.context.audio.currentTime=15;const second=e.run('flushPlaybackSession()');
    assert.equal(e.posts.length,1);e.context.state.summary.library='/other';
    pending.resolve({enabled:true});await first;await second;
    assert.equal(e.posts.length,2);assert.equal(e.posts[1].body.library_path,'/music');
    assert.equal(e.posts[1].body.session.position_seconds,15);
    await e.run('flushPlaybackSession()');assert.equal(e.posts.length,2);
  });
  await test('large queues use a compact closing checkpoint within browser keepalive limits', async()=>{
    const e=environment();active(e);
    e.run("setQueue([{id:'b'},...Array.from({length:2000},(_,i)=>({id:('000000000000000000000000'+i).slice(-24)}))],'b')");
    await e.run('flushPlaybackSession(true)');
    assert.equal(e.posts.length,2);assert.equal(e.posts[0].options.keepalive,false);
    assert.equal(e.posts[1].options.keepalive,true);
    assert.equal(e.posts[1].body.progress.queue_revision,e.posts[0].body.session.queue_revision);
    assert.ok(JSON.stringify(e.posts[1].body).length<1000);
    assert.ok(e.posts[1].body.checkpoint_at>e.posts[0].body.checkpoint_at);
  });
  process.stdout.write(JSON.stringify(cases));
  process.exitCode=cases.every(item=>item.passed)?0:1;
})();
"""


@unittest.skipUnless(shutil.which("node"), "JavaScript checks require Node")
class FrontendSessionTest(unittest.TestCase):
    def test_session_and_races(self):
        result = subprocess.run(
            ["node", "-e", SCRIPT, str(Path(__file__).resolve().parent.parent)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        self.assertTrue(result.stdout, result.stderr)
        cases = json.loads(result.stdout)
        self.assertEqual(len(cases), 11)
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertTrue(case["passed"], case.get("error"))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
