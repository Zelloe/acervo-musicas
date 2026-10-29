"use strict";

const searchState = {scope:"biblioteca", timer:null, controller:null, serial:0, items:[], active:-1};
const searchInput = $('#search');
const searchPanel = $('#search-suggestions');

function closeSearch() {
  clearTimeout(searchState.timer);
  searchState.controller?.abort();
  searchState.serial++;
  searchState.items=[];
  searchState.active=-1;
  searchPanel.hidden=true;
  searchInput.setAttribute("aria-expanded","false");
  searchInput.removeAttribute("aria-activedescendant");
}

function searchMessage(message) {
  searchPanel.innerHTML=`<div class="search-message" role="status">${escapeHTML(message)}</div>`;
  searchPanel.hidden=false;
  searchInput.setAttribute("aria-expanded","true");
}

function showSearchSuggestions(data) {
  const global=searchState.scope==="global";
  const groups=global ? [
    ["Artistas",(data.artistas||[]).map(a=>({type:"global-artist",label:a.nome,detail:"Ver álbuns para baixar",name:"music"}))],
    ["Sugestões de busca",(data.termos||[]).map(text=>({type:"global-term",label:text,detail:"Buscar músicas no catálogo global",name:"search"}))]
  ] : [
    ["Artistas",(data.artistas||[]).map(a=>({type:"artist",label:a.nome,detail:`${number(a.faixas)} faixas na biblioteca`,name:"music"}))],
    ["Álbuns",(data.albuns||[]).map(a=>({type:"album",label:a.titulo,detail:a.artista,id:a.track_id,name:"disc"}))],
    ["Músicas",(data.musicas||[]).map(t=>({type:"song",label:t.titulo,detail:t.artista,id:t.id,name:"music"}))]
  ];
  searchState.items=[];
  searchState.active=-1;
  const html=groups.filter(([,items])=>items.length).map(([title,items])=>`<div class="search-group-title" role="presentation">${title}</div>${items.map(item=>{
    const index=searchState.items.push(item)-1;
    return `<button type="button" role="option" aria-selected="false" tabindex="-1" id="search-option-${index}" data-search-option="${index}">${icon(item.name)}<span><strong>${escapeHTML(item.label)}</strong><small>${escapeHTML(item.detail)}</small></span>${icon("arrow")}</button>`;
  }).join("")}`).join("");
  if(!html){searchMessage(global?"Sem sugestões. Pressione Enter para buscar músicas.":"Nenhuma sugestão. Pressione Enter para ver os resultados.");return;}
  searchPanel.innerHTML=html;
  searchPanel.hidden=false;
  searchInput.setAttribute("aria-expanded","true");
  searchInput.removeAttribute("aria-activedescendant");
}

function scheduleSuggestions() {
  closeSearch();
  const query=searchInput.value.trim();
  if(!query || (searchState.scope==="global"&&query.length<2))return;
  const serial=searchState.serial;
  const scope=searchState.scope;
  searchState.timer=setTimeout(async()=>{
    searchMessage(scope==="global"?"Buscando sugestões no catálogo global…":"Buscando na sua biblioteca…");
    const controller=new AbortController();
    searchState.controller=controller;
    try {
      const params=new URLSearchParams({q:query,escopo:scope});
      const response=await fetch(`/api/sugestoes?${params}`,{signal:controller.signal});
      const data=await response.json();
      if(serial!==searchState.serial || scope!==searchState.scope || query!==searchInput.value.trim())return;
      if(!response.ok)throw new Error(data.erro||"Não foi possível carregar sugestões.");
      showSearchSuggestions(data);
    }catch(error){
      if(error.name==="AbortError" || serial!==searchState.serial)return;
      searchMessage(error.message+" Você pode tentar a busca com Enter.");
    }
  },scope==="global"?350:150);
}

function setSearchScope(scope) {
  closeSearch();
  searchState.scope=scope;
  document.querySelectorAll('[data-search-scope]').forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.searchScope===scope)));
  const label=scope==="global"?"Buscar músicas ou artistas no catálogo global":"Buscar músicas, artistas ou álbuns";
  searchInput.placeholder=label;
  searchInput.setAttribute("aria-label",label);
  searchInput.focus();
  scheduleSuggestions();
}

async function searchLibrary(query, artist="", albumId="", label=query) {
  closeSearch();
  state.search=query;
  state.searchArtist=artist;
  state.searchAlbum=albumId;
  state.searchLabel=label;
  state.filter="todas";
  searchInput.value=label;
  await navigate("biblioteca");
}

async function selectSearchSuggestion(index) {
  const item=searchState.items[index];
  if(!item)return;
  closeSearch();
  if(item.type==="artist")await searchLibrary("",item.label,"",item.label);
  else if(item.type==="album")await searchLibrary("","",item.id,`${item.label} · ${item.detail}`);
  else if(item.type==="song")await showDetails(item.id);
  else await searchFromGlobal(item.type==="global-artist"?"artista":"musica",item.label);
}

function highlightSearch(index) {
  searchState.active=index;
  searchPanel.querySelectorAll('[data-search-option]').forEach((button,i)=>{
    button.setAttribute("aria-selected",String(i===index));
    if(i===index){searchInput.setAttribute("aria-activedescendant",button.id);button.scrollIntoView({block:"nearest"});}
  });
}

$('#search-form').addEventListener("submit",async event=>{
  event.preventDefault();
  const query=searchInput.value.trim();
  try {
    if(searchState.active>=0 && !searchPanel.hidden){await selectSearchSuggestion(searchState.active);return;}
    closeSearch();
    if(searchState.scope==="biblioteca")await searchLibrary(query);
    else if(query)await searchFromGlobal("musica",query);
  }catch(error){toast(error.message);}
});
searchInput.addEventListener("input",()=>{
  scheduleSuggestions();
  if(searchState.scope==="biblioteca" && ["revisao","albuns"].includes(state.view)){
    state.search=searchInput.value;
    state.searchArtist="";state.searchAlbum="";state.searchLabel="";state.page=0;
    render();
  }
});
searchInput.addEventListener("focus",scheduleSuggestions);
searchInput.addEventListener("keydown",event=>{
  if(event.key==="Escape"){event.preventDefault();closeSearch();return;}
  if(["ArrowDown","ArrowUp"].includes(event.key)&&!searchPanel.hidden&&searchState.items.length){
    event.preventDefault();
    const delta=event.key==="ArrowDown"?1:-1;
    const index=searchState.active<0?(delta>0?0:searchState.items.length-1):(searchState.active+delta+searchState.items.length)%searchState.items.length;
    highlightSearch(index);
  }
});
searchPanel.addEventListener("pointerdown",event=>event.preventDefault());
searchPanel.addEventListener("click",event=>{
  const button=event.target.closest('[data-search-option]');
  if(button)selectSearchSuggestion(Number(button.dataset.searchOption)).catch(error=>toast(error.message));
});
document.addEventListener("click",event=>{
  const button=event.target.closest('[data-search-scope]');
  if(button)setSearchScope(button.dataset.searchScope);
  else if(!event.target.closest('#search-area'))closeSearch();
});
document.addEventListener("focusin",event=>{if(!event.target.closest('#search-area'))closeSearch();});
window.addEventListener("hashchange",closeSearch);
