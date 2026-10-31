"use strict";

const lyricsState={track:null,serial:0,controller:null,lines:[],active:-1,cache:new Map(),returnFocus:null,result:null,editor:false,draft:"",pending:null,seenJob:null};
const lyricLabel=message=>typeof tr==="function"?tr(message):message;

function lyricsStatus(message, retry=false) {
  lyricsState.statusMessage=message;lyricsState.retry=retry;
  $('#lyrics-status').innerHTML=`${escapeHTML(lyricLabel(message))}${retry?` <button class="text-button" data-action="retry-lyrics">${escapeHTML(lyricLabel("Tentar novamente"))}</button>`:""}`;
}

function closeLyrics() {
  lyricsState.serial++;
  lyricsState.controller?.abort();
  $('#lyrics-panel').hidden=true;
  $('#lyrics-toggle').setAttribute("aria-expanded","false");
  lyricsState.returnFocus?.focus?.();
}

function setLyricsTrack(track) {
  $('#lyrics-toggle').disabled=!track;
  if(!track){closeLyrics();lyricsState.track=null;return;}
  if(!$('#lyrics-panel').hidden)loadLyrics(track);
}

function displayLyrics(data) {
  lyricsState.result=data;
  lyricsState.editor=false;
  $('#lyrics-edit').disabled=false;
  $('#lyrics-edit').textContent=lyricLabel("Editar e salvar letra");
  lyricsState.lines=(data.synced||[]).filter(line=>Number.isFinite(line.t)&&line.t>=0&&typeof line.line==="string");
  lyricsState.active=-1;
  const body=$('#lyrics-body');
  body.scrollTop=0;
  $('#lyrics-follow').parentElement.hidden=!lyricsState.lines.length;
  if(data.instrumental){
    lyricsStatus("Faixa instrumental — sem letra.");body.textContent="";
  }else if(data.found===false||(!lyricsState.lines.length&&!data.text)){
    lyricsStatus("Letra não encontrada para esta faixa.");body.textContent="";
  }else if(lyricsState.lines.length){
    lyricsStatus("Letra sincronizada");
    body.innerHTML=`<div class="lyrics-lines">${lyricsState.lines.map((line,i)=>`<p data-lyric-line="${i}">${escapeHTML(line.line)||"♪"}</p>`).join("")}</div>`;
  }else{
    lyricsStatus("Letra sem sincronização");
    body.innerHTML=`<pre class="lyrics-plain">${escapeHTML(data.text)}</pre>`;
  }
  const source=data.source==="file"?lyricLabel("Do arquivo"):data.source==="lrclib"?"LRCLIB":"";
  let url="";
  try{const parsed=new URL(data.url);if(parsed.origin==="https://lrclib.net")url=parsed.href;}catch{}
  $('#lyrics-source').innerHTML=source?`${escapeHTML(lyricLabel("Fonte:"))} ${url?`<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(source)}</a>`:escapeHTML(source)}`:"";
  updateLyricsLine();
}

async function loadLyrics(track) {
  const serial=++lyricsState.serial;
  lyricsState.controller?.abort();
  lyricsState.track=track;
  lyricsState.editor=false;lyricsState.result=null;lyricsState.draft="";
  $('#lyrics-edit').disabled=false;
  lyricsState.lines=[];lyricsState.active=-1;
  $('#lyrics-heading').dataset.i18nUser="true";$('#lyrics-heading').textContent=join(track.title);
  $('#lyrics-artist').dataset.i18nUser="true";$('#lyrics-artist').textContent=join(track.artist);
  $('#lyrics-body').textContent="";$('#lyrics-source').textContent="";
  $('#lyrics-follow').parentElement.hidden=true;
  lyricsStatus("Buscando letra…");
  const key=`${track.id}:${state.summary?.library_revision||""}`;
  if(lyricsState.cache.has(key)){displayLyrics(lyricsState.cache.get(key));return;}
  const controller=new AbortController();lyricsState.controller=controller;
  try{
    const response=await fetch(`/api/lyrics?id=${encodeURIComponent(track.id)}`,{signal:controller.signal});
    const data=await response.json();
    if(serial!==lyricsState.serial||$('#lyrics-panel').hidden)return;
    if(!response.ok)throw new Error(data.error||lyricLabel("Não foi possível carregar a letra."));
    lyricsState.cache.set(key,data);
    while(lyricsState.cache.size>30)lyricsState.cache.delete(lyricsState.cache.keys().next().value);
    displayLyrics(data);
  }catch(error){
    if(error.name==="AbortError"||serial!==lyricsState.serial)return;
    lyricsStatus("Não foi possível carregar a letra.",true);
    $('#lyrics-body').textContent=error.message;
  }
}

function editLyrics() {
  if(!lyricsState.track)return;
  lyricsState.serial++;lyricsState.controller?.abort();
  if(lyricsState.editor){displayLyrics(lyricsState.result||{found:false});return;}
  lyricsState.editor=true;
  const text=lyricsState.draft||lyricsState.result?.text||"";
  lyricsState.draft=text;
  $('#lyrics-edit').textContent=lyricLabel("Voltar à letra");
  $('#lyrics-follow').parentElement.hidden=true;
  lyricsStatus("Confira ou cole a letra simples antes de salvar.");
  $('#lyrics-body').innerHTML=`<form id="lyrics-form"><label for="lyrics-editor">${escapeHTML(lyricLabel("Letra sem marcações de tempo"))}</label><textarea id="lyrics-editor" maxlength="100000" rows="12">${escapeHTML(text)}</textarea><p>${escapeHTML(lyricLabel("A gravação exige confirmação e cria um backup completo. A letra sincronizada continua disponível para reprodução."))}</p><button type="submit" class="button primary" data-action="preview-lyrics">${escapeHTML(lyricLabel("Revisar antes de salvar"))}</button></form>`;
  setWriteControls();$('#lyrics-editor').focus();
}

async function previewLyrics(event) {
  event.preventDefault();
  const button=event.target.querySelector('[type="submit"]');button.disabled=true;
  lyricsState.draft=$('#lyrics-editor').value;
  try{
    const preview=await api("/api/lyrics/preview",{id:lyricsState.track.id,text:lyricsState.draft});
    lyricsState.pending=preview;
    modal(`${modalHead(lyricLabel("Salvar letra no arquivo?"),escapeHTML(join(preview.artist)+" — "+join(preview.title)))}<div class="modal-body"><div class="backup-note">${icon("shield")}<span>${escapeHTML(lyricLabel("Será criado um backup completo. Só a letra simples será alterada; você pode desfazer pelo Histórico."))}</span></div><h3>${escapeHTML(lyricLabel("Letra conferida"))}</h3><pre class="lyrics-review">${escapeHTML(preview.text)}</pre>${preview.current.length?`<details><summary>${escapeHTML(lyricLabel("Letra atual do arquivo"))}</summary><pre class="lyrics-review">${escapeHTML(preview.current.join("\n\n"))}</pre></details>`:""}<p class="source">${escapeHTML(preview.file)}</p></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${escapeHTML(lyricLabel("Voltar à revisão"))}</button><button class="button primary" data-action="confirm-lyrics">${escapeHTML(lyricLabel("Criar backup e salvar letra"))}</button></div>`);
  }catch(error){toast(error.message);}finally{button.disabled=false;}
}

async function captureLyricsJob(job) {
  if(!job||job.status!=="completed"||!job.result?.lyrics_track_id||lyricsState.seenJob===job.id)return;
  lyricsState.seenJob=job.id;
  const ident=job.result.lyrics_track_id;
  for(const key of lyricsState.cache.keys())if(key.startsWith(ident+":"))lyricsState.cache.delete(key);
  toast(lyricLabel("Letra salva no arquivo. Você pode desfazer pelo Histórico."));
  if(!$('#lyrics-panel').hidden&&lyricsState.track?.id===ident)await loadLyrics(lyricsState.track);
}

async function showLyrics(id) {
  if(!id)return;
  lyricsState.returnFocus=document.activeElement;
  if($('#modal').open)$('#modal').close();
  $('#lyrics-panel').hidden=false;
  $('#lyrics-toggle').setAttribute("aria-expanded","true");
  $('#lyrics-close').focus();
  const serial=++lyricsState.serial;
  lyricsStatus("Buscando letra…");
  try{
    const track=state.current?.id===id?state.current:await api(`/api/track?id=${encodeURIComponent(id)}`);
    if(serial!==lyricsState.serial)return;
    await loadLyrics(track);
  }catch(error){if(serial===lyricsState.serial){lyricsStatus("Não foi possível carregar a letra.");$('#lyrics-body').textContent=error.message;}}
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
    api("/api/lyrics/save",{preview:lyricsState.pending.preview}).then(async()=>{lyricsState.pending=null;$('#modal').close();await updateState();toast(lyricLabel("Criando backup e salvando a letra…"));}).catch(error=>{button.disabled=false;toast(error.message);});
  }
});
document.addEventListener("submit",event=>{if(event.target.id==="lyrics-form")previewLyrics(event);});
document.addEventListener("input",event=>{if(event.target.id==="lyrics-editor")lyricsState.draft=event.target.value;});
document.addEventListener("keydown",event=>{if(event.key==="Escape"&&!$('#lyrics-panel').hidden&&!$('#modal').open){event.preventDefault();closeLyrics();}});
