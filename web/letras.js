"use strict";

const lyricsState={track:null,serial:0,controller:null,lines:[],active:-1,cache:new Map(),returnFocus:null};
const lyricLabel=message=>typeof t==="function"?t(message):message;

function lyricsStatus(message, retry=false) {
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
  lyricsState.lines=(data.sincronizada||[]).filter(line=>Number.isFinite(line.t)&&line.t>=0&&typeof line.linha==="string");
  lyricsState.active=-1;
  const body=$('#lyrics-body');
  body.scrollTop=0;
  $('#lyrics-follow').parentElement.hidden=!lyricsState.lines.length;
  if(data.instrumental){
    lyricsStatus("Faixa instrumental — sem letra.");body.textContent="";
  }else if(data.encontrada===false||(!lyricsState.lines.length&&!data.texto)){
    lyricsStatus("Letra não encontrada para esta faixa.");body.textContent="";
  }else if(lyricsState.lines.length){
    lyricsStatus("Letra sincronizada");
    body.innerHTML=`<div class="lyrics-lines">${lyricsState.lines.map((line,i)=>`<p data-lyric-line="${i}">${escapeHTML(line.linha)||"♪"}</p>`).join("")}</div>`;
  }else{
    lyricsStatus("Letra sem sincronização");
    body.innerHTML=`<pre class="lyrics-plain">${escapeHTML(data.texto)}</pre>`;
  }
  const source=data.fonte==="arquivo"?lyricLabel("Do arquivo"):data.fonte==="lrclib"?"LRCLIB":"";
  let url="";
  try{const parsed=new URL(data.url);if(parsed.origin==="https://lrclib.net")url=parsed.href;}catch{}
  $('#lyrics-source').innerHTML=source?`${escapeHTML(lyricLabel("Fonte:"))} ${url?`<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(source)}</a>`:escapeHTML(source)}`:"";
  updateLyricsLine();
}

async function loadLyrics(track) {
  const serial=++lyricsState.serial;
  lyricsState.controller?.abort();
  lyricsState.track=track;
  lyricsState.lines=[];lyricsState.active=-1;
  $('#lyrics-heading').textContent=join(track.titulo);
  $('#lyrics-artist').textContent=join(track.artista);
  $('#lyrics-body').textContent="";$('#lyrics-source').textContent="";
  $('#lyrics-follow').parentElement.hidden=true;
  lyricsStatus("Buscando letra…");
  const key=`${track.id}:${state.summary?.biblioteca_revisao||""}`;
  if(lyricsState.cache.has(key)){displayLyrics(lyricsState.cache.get(key));return;}
  const controller=new AbortController();lyricsState.controller=controller;
  try{
    const response=await fetch(`/api/letra?id=${encodeURIComponent(track.id)}`,{signal:controller.signal});
    const data=await response.json();
    if(serial!==lyricsState.serial||$('#lyrics-panel').hidden)return;
    if(!response.ok)throw new Error(data.erro||lyricLabel("Não foi possível carregar a letra."));
    lyricsState.cache.set(key,data);
    while(lyricsState.cache.size>30)lyricsState.cache.delete(lyricsState.cache.keys().next().value);
    displayLyrics(data);
  }catch(error){
    if(error.name==="AbortError"||serial!==lyricsState.serial)return;
    lyricsStatus("Não foi possível carregar a letra.",true);
    $('#lyrics-body').textContent=error.message;
  }
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
  if($('#lyrics-panel').hidden||!lyricsState.lines.length||state.current?.id!==lyricsState.track?.id)return;
  const seconds=$('#audio').currentTime;
  let lo=0,hi=lyricsState.lines.length-1,index=-1;
  while(lo<=hi){const mid=(lo+hi)>>1;if(lyricsState.lines[mid].t<=seconds){index=mid;lo=mid+1;}else hi=mid-1;}
  if(index===lyricsState.active)return;
  lyricsState.active=index;
  document.querySelectorAll('[data-lyric-line]').forEach(line=>{const active=Number(line.dataset.lyricLine)===index;line.classList.toggle("current",active);if(active)line.setAttribute("aria-current","true");else line.removeAttribute("aria-current");});
  const current=$(`[data-lyric-line="${index}"]`);
  if(current&&$('#lyrics-follow').checked){const body=$('#lyrics-body');body.scrollTo({top:Math.max(0,current.offsetTop-body.clientHeight/2+current.clientHeight/2),behavior:"auto"});}
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
});
document.addEventListener("keydown",event=>{if(event.key==="Escape"&&!$('#lyrics-panel').hidden&&!$('#modal').open){event.preventDefault();closeLyrics();}});
