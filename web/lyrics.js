"use strict";

const lyricsState={track:null,serial:0,controller:null,lines:[],active:-1,cache:new Map(),returnFocus:null,result:null,editor:false,manual:false,syncDraft:null,draft:"",pending:null,seenJob:null};
const lyricLabel=message=>typeof tr==="function"?tr(message):message;

function lyricsStatus(message, retry=false) {
  lyricsState.statusMessage=message;lyricsState.retry=retry;
  $('#lyrics-status').innerHTML=`${escapeHTML(lyricLabel(message))}${retry?` <button class="text-button" data-action="retry-lyrics">${escapeHTML(lyricLabel("Try again"))}</button>`:""}`;
}

function closeLyrics() {
  lyricsState.serial++;
  lyricsState.controller?.abort();
  lyricsState.manual=false;
  $('#lyrics-panel').hidden=true;
  $('#lyrics-toggle').setAttribute("aria-expanded","false");
  lyricsState.returnFocus?.focus?.();
}

function setLyricsTrack(track) {
  $('#lyrics-toggle').disabled=!track;
  if(!track){closeLyrics();lyricsState.track=null;return;}
  if(lyricsState.manual&&lyricsState.track?.id===track.id){lyricsState.track=track;return;}
  lyricsState.manual=false;
  if(!$('#lyrics-panel').hidden)loadLyrics(track);
}

function displayLyrics(data) {
  lyricsState.result=data;
  lyricsState.editor=false;lyricsState.manual=false;
  $('#lyrics-sync').hidden=false;$('#lyrics-body').classList.remove("sync-editor");
  $('#lyrics-edit').disabled=false;$('#lyrics-sync').disabled=false;
  $('#lyrics-edit').textContent=lyricLabel("Edit and save lyrics");
  lyricsState.lines=(data.synced||[]).filter(line=>Number.isFinite(line.t)&&line.t>=0&&typeof line.line==="string");
  lyricsState.active=-1;
  const body=$('#lyrics-body');
  body.scrollTop=0;
  $('#lyrics-follow').parentElement.hidden=!lyricsState.lines.length;
  if(data.instrumental){
    lyricsStatus("Instrumental track — no lyrics.");body.textContent="";
  }else if(data.found===false||(!lyricsState.lines.length&&!data.text)){
    lyricsStatus("No lyrics found for this track.");body.textContent="";
  }else if(lyricsState.lines.length){
    lyricsStatus("Synced lyrics");
    body.innerHTML=`<div class="lyrics-lines">${lyricsState.lines.map((line,i)=>`<p data-lyric-line="${i}">${escapeHTML(line.line)||"♪"}</p>`).join("")}</div>`;
  }else{
    lyricsStatus("Unsynced lyrics");
    body.innerHTML=`<pre class="lyrics-plain">${escapeHTML(data.text)}</pre>`;
  }
  const source=data.source==="file"?lyricLabel("From file"):data.source==="lrclib"?"LRCLIB":"";
  let url="";
  try{const parsed=new URL(data.url);if(parsed.origin==="https://lrclib.net")url=parsed.href;}catch{}
  $('#lyrics-source').innerHTML=source?`${escapeHTML(lyricLabel("Source:"))} ${url?`<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(source)}</a>`:escapeHTML(source)}`:"";
  updateLyricsLine();
}

async function loadLyrics(track) {
  const serial=++lyricsState.serial;
  lyricsState.controller?.abort();
  lyricsState.track=track;
  lyricsState.editor=false;lyricsState.manual=false;lyricsState.result=null;lyricsState.draft="";
  $('#lyrics-sync').hidden=false;$('#lyrics-body').classList.remove("sync-editor");
  $('#lyrics-edit').disabled=false;$('#lyrics-sync').disabled=false;
  lyricsState.lines=[];lyricsState.active=-1;
  $('#lyrics-heading').dataset.i18nUser="true";$('#lyrics-heading').textContent=join(track.title);
  $('#lyrics-artist').dataset.i18nUser="true";$('#lyrics-artist').textContent=join(track.artist);
  $('#lyrics-body').textContent="";$('#lyrics-source').textContent="";
  $('#lyrics-follow').parentElement.hidden=true;
  lyricsStatus("Finding lyrics…");
  const key=`${track.id}:${state.summary?.library_revision||""}`;
  if(lyricsState.cache.has(key)){displayLyrics(lyricsState.cache.get(key));return;}
  const controller=new AbortController();lyricsState.controller=controller;
  try{
    const response=await fetch(`/api/lyrics?id=${encodeURIComponent(track.id)}`,{signal:controller.signal,headers:languageHeaders()});
    const data=await response.json();
    if(serial!==lyricsState.serial||$('#lyrics-panel').hidden)return;
    if(!response.ok)throw new Error(data.error||lyricLabel("Could not load lyrics."));
    lyricsState.cache.set(key,data);
    while(lyricsState.cache.size>30)lyricsState.cache.delete(lyricsState.cache.keys().next().value);
    displayLyrics(data);
  }catch(error){
    if(error.name==="AbortError"||serial!==lyricsState.serial)return;
    lyricsStatus("Could not load lyrics.",true);
    $('#lyrics-body').textContent=error.message;
  }
}

function editLyrics() {
  if(lyricsState.manual&&typeof leaveManualSync==="function"){leaveManualSync();return;}
  if(!lyricsState.track)return;
  lyricsState.serial++;lyricsState.controller?.abort();
  if(lyricsState.editor){displayLyrics(lyricsState.result||{found:false});return;}
  lyricsState.editor=true;
  const text=lyricsState.draft||(lyricsState.result?.synced?.length?formatSyncedLyrics(lyricsState.result.synced):lyricsState.result?.text)||"";
  $('#lyrics-sync').hidden=false;$('#lyrics-body').classList.remove("sync-editor");
  lyricsState.draft=text;
  $('#lyrics-edit').textContent=lyricLabel("Back to lyrics");
  $('#lyrics-follow').parentElement.hidden=true;
  lyricsStatus("Review or paste lyrics before saving.");
  $('#lyrics-body').innerHTML=`<form id="lyrics-form"><label for="lyrics-editor">${escapeHTML(lyricLabel("Lyrics (plain text or LRC)"))}</label><textarea id="lyrics-editor" maxlength="100000" rows="12">${escapeHTML(text)}</textarea><p>${escapeHTML(lyricLabel("Lyrics and any timecodes are saved inside the file, with a full backup and undo."))}</p><button type="submit" class="button primary" data-action="preview-lyrics">${escapeHTML(lyricLabel("Review before saving"))}</button></form>`;
  setWriteControls();$('#lyrics-editor').focus();
}

function formatSyncedLyrics(lines) {
  return lines.filter(line=>Number.isFinite(line.t)&&line.t>=0&&typeof line.line==="string").map(line=>{
    const ticks=Math.round(line.t*100),minutes=Math.floor(ticks/6000),seconds=Math.floor(ticks%6000/100),centiseconds=ticks%100;
    return `[${String(minutes).padStart(2,"0")}:${String(seconds).padStart(2,"0")}.${String(centiseconds).padStart(2,"0")}]${line.line}`;
  }).join("\n");
}

async function reviewLyricsText(text,button) {
  if(!lyricsState.track)return;
  const trackId=lyricsState.track.id;
  if(button)button.disabled=true;
  try{
    const preview=await api("/api/lyrics/preview",{id:trackId,text});
    if(lyricsState.track?.id!==trackId)return;
    lyricsState.pending=preview;lyricsState.draft=preview.text;
    const format=preview.format==="synced"?lyricLabel("Synced (LRC)"):lyricLabel("Plain text");
    modal(`${modalHead(lyricLabel("Save lyrics to the file?"),escapeHTML(join(preview.artist)+" — "+join(preview.title)))}<div class="modal-body"><div class="backup-note">${icon("shield")}<span>${escapeHTML(lyricLabel("A full backup will be created. Lyrics and any timecodes will be saved in the file; you can undo this in History."))}</span></div><span class="chip">${escapeHTML(format)}</span><h3>${escapeHTML(lyricLabel("Reviewed lyrics"))}</h3><pre class="lyrics-review">${escapeHTML(preview.text)}</pre>${preview.current.length?`<details><summary>${escapeHTML(lyricLabel("Current lyrics in the file"))}</summary><pre class="lyrics-review">${escapeHTML(preview.current.join("\n\n"))}</pre></details>`:""}<p class="source">${escapeHTML(preview.file)}</p></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${escapeHTML(lyricLabel("Back to review"))}</button><button class="button primary" data-action="confirm-lyrics">${escapeHTML(lyricLabel("Back up and save lyrics"))}</button></div>`);
  }catch(error){toast(error.message);}finally{if(button?.isConnected)button.disabled=false;setWriteControls();}
}

async function previewLyrics(event) {
  event.preventDefault();
  lyricsState.draft=$('#lyrics-editor').value;
  await reviewLyricsText(lyricsState.draft,event.target.querySelector('[type="submit"]'));
}

async function captureLyricsJob(job) {
  if(!job||job.status!=="completed"||lyricsState.seenJob===job.id)return;
  const ids=Array.isArray(job.result?.lyrics_track_ids)?job.result.lyrics_track_ids:job.result?.lyrics_track_id?[job.result.lyrics_track_id]:[];
  if(!ids.length)return;
  lyricsState.seenJob=job.id;
  for(const key of lyricsState.cache.keys())if(ids.some(ident=>key.startsWith(ident+":")))lyricsState.cache.delete(key);
  if(job.result.lyrics_track_id)toast(lyricLabel("Lyrics saved to the file. You can undo this in History."));
  if(!$('#lyrics-panel').hidden&&ids.includes(lyricsState.track?.id))await loadLyrics(lyricsState.track);
}

async function showLyrics(id) {
  if(!id)return;
  lyricsState.returnFocus=document.activeElement;$('#lyrics-sync').disabled=true;
  if($('#modal').open)$('#modal').close();
  $('#lyrics-panel').hidden=false;
  $('#lyrics-toggle').setAttribute("aria-expanded","true");
  $('#lyrics-close').focus();
  const serial=++lyricsState.serial;
  lyricsStatus("Finding lyrics…");
  try{
    const track=state.current?.id===id?state.current:await api(`/api/track?id=${encodeURIComponent(id)}`);
    if(serial!==lyricsState.serial)return;
    await loadLyrics(track);
  }catch(error){if(serial===lyricsState.serial){lyricsStatus("Could not load lyrics.");$('#lyrics-body').textContent=error.message;}}
}

function updateLyricsLine() {
  if($('#lyrics-panel').hidden||lyricsState.editor||!lyricsState.lines.length||state.current?.id!==lyricsState.track?.id)return;
  const seconds=$('#audio').currentTime;
  let lo=0,hi=lyricsState.lines.length-1,index=-1;
  while(lo<=hi){const mid=(lo+hi)>>1;if(lyricsState.lines[mid].t<=seconds){index=mid;lo=mid+1;}else hi=mid-1;}
  if(index===lyricsState.active)return;
  lyricsState.active=index;
  document.querySelectorAll('[data-lyric-line]').forEach(line=>{const active=Number(line.dataset.lyricLine)===index;line.classList.toggle("current",active);if(active)line.setAttribute("aria-current","true");else line.removeAttribute("aria-current");});
  const current=$(`[data-lyric-line="${index}"]`);
  if(current&&$('#lyrics-follow').checked){const body=$('#lyrics-body');body.scrollTo({top:Math.max(0,current.offsetTop-body.clientHeight/2+current.clientHeight/2),behavior:"auto"});}
}

function refreshLyricsLanguage() {
  if($('#lyrics-panel').hidden)return;
  if(lyricsState.manual&&typeof renderManualSync==="function"){renderManualSync();return;}
  if(lyricsState.editor){
    lyricsState.draft=$('#lyrics-editor')?.value||lyricsState.draft;
    lyricsState.editor=false;editLyrics();
  }else if(lyricsState.result){displayLyrics(lyricsState.result);}
  else if(lyricsState.statusMessage){lyricsStatus(lyricsState.statusMessage,lyricsState.retry);}
}

document.addEventListener("DOMContentLoaded",()=>{
  $('#lyrics-toggle').addEventListener("click",()=>{if($('#lyrics-panel').hidden)showLyrics(state.current?.id);else closeLyrics();});
  $('#lyrics-close').addEventListener("click",closeLyrics);
  $('#audio').addEventListener("timeupdate",updateLyricsLine);
  $('#audio').addEventListener("seeked",updateLyricsLine);
});
document.addEventListener("click",event=>{
  const button=event.target.closest("button");if(!button||button.disabled)return;
  if(button.dataset.lyrics)showLyrics(button.dataset.lyrics);
  else if(button.dataset.action==="retry-lyrics"&&lyricsState.track)loadLyrics(lyricsState.track);
  else if(button.dataset.action==="edit-lyrics")editLyrics();
  else if(button.dataset.action==="confirm-lyrics"&&lyricsState.pending){
    button.disabled=true;
    api("/api/lyrics/save",{preview:lyricsState.pending.preview}).then(async()=>{lyricsState.pending=null;$('#modal').close();await updateState();toast(lyricLabel("Creating a backup and saving lyrics…"));}).catch(error=>{button.disabled=false;toast(error.message);});
  }
});
document.addEventListener("submit",event=>{if(event.target.id==="lyrics-form")previewLyrics(event);});
document.addEventListener("input",event=>{if(event.target.id==="lyrics-editor")lyricsState.draft=event.target.value;});
document.addEventListener("keydown",event=>{if(event.key==="Escape"&&!$('#lyrics-panel').hidden&&!$('#modal').open){event.preventDefault();closeLyrics();}});
