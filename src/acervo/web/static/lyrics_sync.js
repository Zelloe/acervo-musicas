"use strict";

function manualSource(text) {
  return String(text||"").split(/\r?\n/).flatMap(raw=>{
    const line=raw.trim();
    if(!line||/^\[(?:ar|ti|al|by|offset|length):/i.test(line))return [];
    const prefix=line.match(/^(?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\])+/)?.[0]||"";
    const verse=line.slice(prefix.length).trim();
    return verse?Array(Math.max(1,(prefix.match(/\[/g)||[]).length)).fill(verse):[];
  });
}

function startManualSync() {
  if(!lyricsState.track)return;
  const editor=lyricsState.editor?$('#lyrics-editor'):null;
  const lines=manualSource(editor?.value??lyricsState.result?.text??"");
  let draft=lyricsState.syncDraft;
  if(!editor&&draft?.trackId===lyricsState.track.id){
    // Reopening the tool resumes timings even if the saved lyrics have changed.
  }else if(!draft||draft.trackId!==lyricsState.track.id||draft.source!==lines.join("\n")){
    draft={trackId:lyricsState.track.id,source:lines.join("\n"),lines,times:lines.map(()=>null),index:0,history:[]};
  }
  if(!draft?.lines.length){if(!lyricsState.editor)editLyrics();toast(lyricLabel("Paste lyrics in the editor before synchronizing."));return;}
  lyricsState.serial++;lyricsState.controller?.abort();lyricsState.syncDraft=draft;lyricsState.manual=true;lyricsState.editor=true;
  $('#lyrics-follow').parentElement.hidden=true;
  renderManualSync();
}

function syncHistory() {
  const draft=lyricsState.syncDraft;
  draft.history.push({times:[...draft.times],index:draft.index});
  if(draft.history.length>200)draft.history.shift();
}

function syncValid() {
  const draft=lyricsState.syncDraft;if(!draft?.lines.length)return false;
  const max=state.current?.id===draft.trackId&&Number.isFinite(audio.duration)?audio.duration:lyricsState.track?.duration_seconds;
  let previous=-1;
  return draft.times.every(time=>{
    if(!Number.isFinite(time)||time<0||time<previous||(Number.isFinite(max)&&time>max+.5))return false;
    previous=time;return true;
  });
}

function manualLRC() {
  const draft=lyricsState.syncDraft;
  return formatSyncedLyrics(draft.lines.map((line,index)=>({t:draft.times[index],line})));
}

function syncClock(seconds) {
  if(!Number.isFinite(seconds))seconds=0;
  const ticks=Math.max(0,Math.round(seconds*100));
  return `${String(Math.floor(ticks/6000)).padStart(2,"0")}:${String(Math.floor(ticks%6000/100)).padStart(2,"0")}.${String(ticks%100).padStart(2,"0")}`;
}

function updateSyncControls() {
  if(!lyricsState.manual||!lyricsState.syncDraft||$('#lyrics-panel').hidden)return;
  const draft=lyricsState.syncDraft,playingThis=state.current?.id===draft.trackId;
  const canMark=playingThis&&audio.readyState>=1&&Number.isFinite(audio.currentTime)&&draft.index<draft.lines.length;
  const busy=state.serverIncompatible||state.summary?.job?.status==="running"||!!state.summary?.lyrics_busy||!!state.summary?.protection_error;
  const mark=$('[data-action="sync-mark"]');if(mark)mark.disabled=!canMark;
  const review=$('[data-action="sync-review"]');if(review)review.disabled=busy||!syncValid();
  const undo=$('[data-action="sync-undo"]');if(undo)undo.disabled=!draft.history.length;
  const back=$('[data-action="sync-previous"]');if(back)back.disabled=draft.index===0;
  const player=$('[data-action="sync-play"]');if(player)player.textContent=lyricLabel(playingThis&&!audio.paused?"Pause":"Play this track");
  const clock=$('#sync-clock');if(clock)clock.textContent=syncClock(playingThis?audio.currentTime:0);
  const count=$('#sync-count');if(count)count.textContent=`${number(draft.times.filter(Number.isFinite).length)} ${lyricLabel("of")} ${number(draft.lines.length)} ${lyricLabel("verses marked")}`;
  const hint=$('#sync-hint');
  if(hint){
    const complete=draft.times.every(Number.isFinite);
    hint.textContent=lyricLabel(complete&&!syncValid()?"Keep the times in order and within the track length.":draft.index>=draft.lines.length?"All verses are marked. Review the timecodes before saving.":!playingThis?"Play this track to mark its verses.":"Press M or Mark verse when the highlighted verse begins.");
  }
}

function renderManualSync(scroll=false) {
  const draft=lyricsState.syncDraft;if(!draft||!lyricsState.manual)return;
  $('#lyrics-body').classList.add("sync-editor");$('#lyrics-sync').hidden=true;
  $('#lyrics-edit').textContent=lyricLabel("Back to editor");
  lyricsStatus("Synchronize lyrics by hand");
  $('#lyrics-body').innerHTML=`<div class="sync-toolbar"><div class="sync-player"><strong id="sync-clock">00:00.00</strong><button class="button small ghost" data-action="sync-play">${lyricLabel("Play this track")}</button></div><div class="sync-buttons"><button class="button primary" data-action="sync-mark">${lyricLabel("Mark verse (M)")}</button><button class="button small ghost" data-action="sync-previous">${lyricLabel("Previous verse")}</button><button class="button small ghost" data-action="sync-undo">${lyricLabel("Undo mark")}</button></div><p id="sync-count"></p><p id="sync-hint" role="status"></p></div><div class="sync-verses">${draft.lines.map((line,index)=>`<div class="sync-verse ${draft.index===index?"current":""}" ${draft.index===index?'aria-current="true"':""} data-sync-verse="${index}"><button class="sync-verse-number" data-sync-select="${index}" aria-label="${lyricLabel("Choose verse")} ${index+1}">${index+1}</button><p>${escapeHTML(line)}</p><label><span class="sr-only">${lyricLabel("Time in seconds for verse")} ${index+1}</span><input class="field sync-time-input" type="number" min="0" step="0.01" data-sync-time="${index}" value="${draft.times[index]===null?"":draft.times[index].toFixed(2)}" placeholder="—"></label></div>`).join("")}</div><div class="sync-save-row"><button class="button ghost" data-action="sync-reset">${lyricLabel("Reset timings")}</button><button class="button primary" data-action="sync-review">${lyricLabel("Review before saving")}</button></div>`;
  setWriteControls();updateSyncControls();
  if(scroll){
    const selected=$(`[data-sync-verse="${Math.min(draft.index,draft.lines.length-1)}"]`),body=$('#lyrics-body'),toolbar=$('.sync-toolbar');
    if(selected)body.scrollTo({top:Math.max(0,selected.getBoundingClientRect().top-body.getBoundingClientRect().top+body.scrollTop-(toolbar?.offsetHeight||0)-16),behavior:"auto"});
  }
}

function markSyncVerse() {
  const draft=lyricsState.syncDraft;
  if(!lyricsState.manual||!draft||state.current?.id!==draft.trackId||audio.readyState<1||!Number.isFinite(audio.currentTime)||draft.index>=draft.lines.length)return;
  syncHistory();draft.times[draft.index]=Math.round(Math.max(0,audio.currentTime)*100)/100;draft.index++;
  renderManualSync(true);
}

function leaveManualSync() {
  if(!lyricsState.manual)return;
  lyricsState.draft=syncValid()?manualLRC():lyricsState.syncDraft.source;
  lyricsState.manual=false;lyricsState.editor=false;
  editLyrics();
}

document.addEventListener("click",async event=>{
  const button=event.target.closest("button");if(!button||button.disabled)return;
  try{
    if(button.dataset.action==="sync-lyrics"){startManualSync();return;}
    if(!lyricsState.manual)return;
    const draft=lyricsState.syncDraft;
    if(button.dataset.syncSelect!==undefined){const index=Number(button.dataset.syncSelect);if(Number.isInteger(index)&&index>=0&&index<draft.lines.length){draft.index=index;renderManualSync();}return;}
    const action=button.dataset.action;
    if(action==="sync-mark")markSyncVerse();
    else if(action==="sync-play"){
      if(state.current?.id!==draft.trackId)await play(draft.trackId);
      else if(audio.paused)await audio.play();else audio.pause();
      updateSyncControls();
    }else if(action==="sync-previous"){draft.index=Math.max(0,draft.index-1);renderManualSync(true);}
    else if(action==="sync-undo"){
      const previous=draft.history.pop();if(previous){draft.times=previous.times;draft.index=previous.index;renderManualSync(true);}
    }else if(action==="sync-reset"){syncHistory();draft.times=draft.lines.map(()=>null);draft.index=0;renderManualSync();}
    else if(action==="sync-review"&&syncValid())await reviewLyricsText(manualLRC(),button);
  }catch(error){toast(error.message);}
});
document.addEventListener("change",event=>{
  if(!lyricsState.manual||!event.target.matches('.sync-time-input'))return;
  const index=Number(event.target.dataset.syncTime),draft=lyricsState.syncDraft;
  if(!Number.isInteger(index)||index<0||index>=draft.lines.length)return;
  syncHistory();draft.times[index]=event.target.value.trim()===""?null:Number(event.target.value);
  updateSyncControls();
});
document.addEventListener("keydown",event=>{
  if(!lyricsState.manual||$('#lyrics-panel').hidden||$('#modal').open||event.repeat||event.isComposing||event.ctrlKey||event.metaKey||event.altKey||event.key.toLowerCase()!=="m")return;
  if(event.target.matches("input,textarea,select")||event.target.closest('[contenteditable="true"]'))return;
  event.preventDefault();markSyncVerse();
});
document.addEventListener("DOMContentLoaded",()=>{
  for(const name of ["timeupdate","seeked","loadedmetadata","play","pause","ended"])$('#audio').addEventListener(name,updateSyncControls);
});
