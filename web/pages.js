"use strict";

const pagesState = {current:null, history:[], cache:new Map(), version:"all", openSerial:0};
const pageViews = new Set(["search","artist","album"]);
const foldName = value=>String(value||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLocaleLowerCase();

function artistLinks(names, scope="library") {
  return (Array.isArray(names)?names:[names]).filter(Boolean).map(name=>`<button class="inline-link" data-page-artist="${escapeHTML(name)}" data-page-scope="${scope}" data-i18n-user="true">${escapeHTML(name)}</button>`).join(" / ");
}

function albumLink(title, id) {
  return `<button class="inline-link" data-page-album="${escapeHTML(id)}">${escapeHTML(title)}</button>`;
}

function globalAlbumLink(title, artist) {
  return title?`<button class="inline-link" data-find-album="${escapeHTML(title)}" data-album-artist="${escapeHTML(artist||"")}" data-i18n-user="true">${escapeHTML(title)}</button>`:"";
}

function pageHash(view) {
  if (!pageViews.has(view) || !pagesState.current) return `#${view}`;
  const values = Object.entries(pagesState.current).filter(([key,value])=>key!=="view" && typeof value==="string" && value);
  return `#${view}?${new URLSearchParams(values)}`;
}

function pageSnapshot() {
  const keys=["search","searchArtist","searchAlbum","searchLabel","filter","versionFilter","sort","sortDirection","page"];
  return {view:state.view, detail:pagesState.current?{...pagesState.current}:null, values:Object.fromEntries(keys.map(key=>[key,state[key]])), scroll:scrollY};
}

async function openPage(view, detail, remember=true) {
  if (remember) {
    pagesState.history.push(pageSnapshot());
    if (pagesState.history.length>30) pagesState.history.shift();
  }
  if (!$('#now-view').hidden) closeNowView();
  if ($('#modal').open) $('#modal').close();
  if (typeof closeSearch==="function") closeSearch();
  pagesState.current={...detail,view};
  pagesState.version="all";
  if (view==="search") $('#search').value=detail.query||"";
  return navigate(view);
}

function routeFromHash() {
  const [view,query=""]=location.hash.slice(1).split("?");
  if (pageViews.has(view)) {
    const detail=Object.fromEntries(new URLSearchParams(query));
    if (detail.query || detail.name || detail.id || detail.catalogId) return openPage(view,detail,false);
    return navigate("library");
  }
  return navigate(view||"home");
}

async function goBackPage() {
  const previous=pagesState.history.pop();
  if (!previous) return navigate("library");
  pagesState.current=previous.detail;
  Object.assign(state,previous.values);
  await navigate(previous.view,previous.values.page||0);
  window.scrollTo({top:previous.scroll});
}

function pageBack() {
  return `<button class="text-button page-back" data-action="page-back">${icon("prev")}${tr("Back")}</button>`;
}

function pageData(key, loader) {
  const now=Date.now(), cached=pagesState.cache.get(key);
  if (cached && now-cached.created<120000) return cached.promise;
  const promise=Promise.resolve().then(loader).catch(error=>{pagesState.cache.delete(key);throw error;});
  pagesState.cache.set(key,{created:now,promise});
  while (pagesState.cache.size>40) pagesState.cache.delete(pagesState.cache.keys().next().value);
  return promise;
}

function pageLibraryParams() {
  const detail=pagesState.current||{};
  if (state.view==="artist") return {artist:detail.name||"",sort:"album"};
  if (state.view==="album" && detail.scope!=="global") return {album_id:detail.id||"",sort:"album"};
  return null;
}

async function localPageTracks(params) {
  const count=(state.page+1)*50, pages=[];
  for(let offset=0;offset<count;offset+=100) pages.push(api(`/api/search?${new URLSearchParams({...params,offset,limit:Math.min(100,count-offset)})}`));
  const loaded=await Promise.all(pages);
  return {total:loaded[0].total,tracks:loaded.flatMap(page=>page.tracks)};
}

function pageTrackList(result) {
  return result.tracks.length ? `${trackTable(result.tracks)}<div class="table-footer"><span id="library-shown">${libraryCount(result.tracks.length,result.total)}</span>${result.tracks.length<result.total?`<span id="library-end" role="status">${tr("Loading…")}</span>`:""}</div>` : empty(tr("No tracks here"),tr("Try another search or change the filters."),"music");
}

function pageSection(title, body, action="") {
  return `<section class="section page-section"><div class="section-heading"><h2>${title}</h2>${action}</div>${body}</section>`;
}

function artistCard(artist, scope) {
  return `<button class="artist-card" data-page-artist="${escapeHTML(artist.name)}" data-page-scope="${scope}">${catalogPhoto(artist.photo,true)}<strong>${escapeHTML(artist.name)}</strong><span>${scope==="library"?`${number(artist.tracks)} ${tr("tracks in your library")}`:tr("Browse albums to download")}</span></button>`;
}

async function renderSearchPage() {
  const detail=pagesState.current||{}, query=detail.query||"", scope=detail.scope||"library", request=state.request;
  const head=pageBack()+heading(tr("Search results"),escapeHTML(query||tr("Search")),tr(scope==="global"?"Global catalog":"Your library"));
  if (!query.trim()) return head+empty(tr("Start with an artist, album or track"),tr("Search tracks, artists or albums"),"search");
  if (scope==="global") return head+`<div id="global-search-artists"></div><div id="global-search-catalog" role="status">${tr("Loading…")}</div>`;
  const [suggestions,result]=await Promise.all([
    api(`/api/suggestions?${new URLSearchParams({q:query})}`),
    api(`/api/search?${new URLSearchParams({q:query,limit:8})}`)
  ]);
  if (request!==state.request) return "";
  state.queue=result.tracks.map(track=>track.id);
  const albums=(suggestions.albums||[]).map(item=>({album:item.title,artist:item.artist,track_id:item.track_id,tracks:item.tracks,year:""}));
  return head+pageSection(tr("Artists"),suggestions.artists?.length?`<div class="artist-grid">${suggestions.artists.map(artist=>artistCard(artist,"library")).join("")}</div>`:empty(tr("No artists found"),"","music"))+
    pageSection(tr("Albums"),albums.length?`<div class="album-grid full">${albums.map(albumCard).join("")}</div>`:empty(tr("No albums found"),"","disc"))+
    pageSection(tr("Tracks"),result.tracks.length?trackTable(result.tracks):empty(tr("No tracks here"),"","music"),result.total>8?`<button class="text-button" data-search-all="${escapeHTML(query)}">${tr("View all")} · ${number(result.total)}</button>`:"");
}

async function renderArtistPage() {
  const detail=pagesState.current||{}, request=state.request, name=detail.name||"";
  const result=await localPageTracks({artist:name,sort:"album"});
  if (request!==state.request) return "";
  state.queue=result.tracks.map(track=>track.id);
  const albums=state.albums.filter(album=>album.artist.split(" / ").some(artist=>foldName(artist)===foldName(name)));
  const actions=result.tracks.length?`<button class="button primary" data-play="${result.tracks[0].id}">${icon("play")}${tr("Play")}</button><button class="button ghost" data-action="page-shuffle">${icon("shuffle")}${tr("Shuffle all")}</button>`:"";
  return pageBack()+`<section class="detail-hero artist-hero"><div class="detail-art" id="artist-photo">${catalogPhoto(detail.photo,true)}</div><div><span class="section-kicker">${tr("Artist")}</span><h1 data-i18n-user="true">${escapeHTML(name)}</h1><p>${number(result.total)} ${tr("tracks in your library")}</p><div class="detail-actions">${actions}</div></div></section>`+
    pageSection(tr("Albums in your library"),albums.length?`<div class="album-grid full">${albums.map(albumCard).join("")}</div>`:empty(tr("No albums found"),"","disc"))+
    pageSection(tr("Tracks in your collection"),pageTrackList(result))+
    pageSection(tr("Discography"),`<div id="artist-discography" data-page-catalog-section role="status">${tr("Loading…")}</div>`);
}

async function renderAlbumPage() {
  const detail=pagesState.current||{}, request=state.request;
  if (detail.scope==="global") {
    const catalog=await storedCatalog(detail.catalogId);
    if (request!==state.request) return "";
    useCatalog(catalog);
    const first=catalog.items[0], art=first?.cover?catalogPhoto(first.cover):icon("disc");
    return pageBack()+`<section class="detail-hero"><div class="detail-art">${art}</div><div><span class="section-kicker">${tr(catalog.type==="editions"?"Choose an edition":"Album")}</span><h1 data-i18n-user="true">${escapeHTML(detail.title||catalog.title)}</h1><p>${artistLinks([detail.artist||catalog.artist],"global")}</p></div></section>`+catalogContent(catalog);
  }
  const result=await localPageTracks({album_id:detail.id,sort:"album"});
  if (request!==state.request) return "";
  state.queue=result.tracks.map(track=>track.id);
  const first=result.tracks[0];
  if (!first) return pageBack()+empty(tr("No tracks here"),tr("Refresh library"),"disc");
  return pageBack()+`<section class="detail-hero"><div class="detail-art">${trackArtwork(first)}</div><div><span class="section-kicker">${tr("Album")}</span><h1 data-i18n-user="true">${escapeHTML(join(first.album))}</h1><p>${artistLinks(first.artist)}</p><p>${escapeHTML((join(first.year).match(/\d{4}/)||[""])[0])} · ${number(result.total)} ${tr("tracks")}</p><div class="detail-actions"><button class="button primary" data-play="${first.id}">${icon("play")}${tr("Play")}</button><button class="button ghost" data-action="page-shuffle">${icon("shuffle")}${tr("Shuffle all")}</button></div></div></section>`+pageTrackList(result);
}

function applyPageVersions() {
  const section=$('#artist-discography');
  if (!section) return;
  section.querySelectorAll('[data-version-row]').forEach(row=>{row.hidden=!recordingMatches({type:row.dataset.versionType,marks:row.dataset.versionMarks.split(" ")},pagesState.version);});
  section.querySelectorAll('[data-page-version]').forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.pageVersion===pagesState.version)));
}

function hydratePage(request) {
  const detail={...pagesState.current}, active=()=>request===state.request;
  const showError=(selector,error)=>{const node=$(selector);if(active()&&node)node.innerHTML=`<p class="download-warning">${escapeHTML(error.message)}</p><button class="button ghost" data-action="page-retry">${tr("Try again")}</button>`;};
  if(state.view==="artist") {
    pageData(`artist:${detail.name}`,()=>browseCatalog('/api/catalog/search',{type:"artist",query:detail.name})).then(catalog=>{
      if(!active())return;
      $('#artist-discography').innerHTML=recordingChips(pagesState.version,"data-page-version")+catalogChoices(catalog,true);
      applyPageVersions();
    }).catch(error=>showError('#artist-discography',error));
    if(!detail.photo) pageData(`photo:${detail.name}`,()=>api(`/api/suggestions?${new URLSearchParams({q:detail.name,scope:"global"})}`)).then(data=>{
      const exact=(data.artists||[]).filter(artist=>foldName(artist.name)===foldName(detail.name));
      if(active()&&exact.length===1)$('#artist-photo').innerHTML=catalogPhoto(exact[0].photo,true);
    }).catch(()=>{});
  } else if(state.view==="search"&&detail.scope==="global"&&detail.query) {
    pageData(`global-artists:${detail.query}`,()=>api(`/api/suggestions?${new URLSearchParams({q:detail.query,scope:"global"})}`)).then(data=>{
      if(active()&&data.artists?.length)$('#global-search-artists').innerHTML=pageSection(tr("Artists"),`<div class="artist-grid">${data.artists.map(artist=>artistCard(artist,"global")).join("")}</div>`);
    }).catch(error=>showError('#global-search-artists',error));
    pageData(`global-tracks:${detail.query}`,()=>browseCatalog('/api/catalog/search',{type:"song",query:detail.query})).then(catalog=>{
      if(!active())return;
      useCatalog(catalog);
      $('#global-search-catalog').innerHTML=catalogContent(catalog);
      applySimpleVersionFilter();setWriteControls();
    }).catch(error=>showError('#global-search-catalog',error));
  }
}

async function openCatalogAlbum(catalogId,itemId) {
  const serial=++pagesState.openSerial, request=state.request;
  const catalog=await storedCatalog(catalogId), item=catalog.items.find(row=>row.id===itemId);
  if(!item)return;
  if(serial!==pagesState.openSerial || request!==state.request)return;
  const next=await browseCatalog('/api/catalog/open',{catalog:catalogId,item:itemId});
  if(request!==state.request || serial!==pagesState.openSerial)return;
  return openPage("album",{scope:"global",catalogId:next.id,title:catalog.type==="albums"?item.title:catalog.title,artist:catalog.artist||item.artist||""});
}

document.addEventListener("click",async event=>{
  const button=event.target.closest('button');if(!button||button.disabled)return;
  const pageRequest=state.request;
  try {
    if(button.dataset.pageArtist) {event.preventDefault();await openPage("artist",{name:button.dataset.pageArtist,scope:button.dataset.pageScope||"library"});}
    else if(button.dataset.pageAlbum) await openPage("album",{id:button.dataset.pageAlbum,scope:"library"});
    else if(button.dataset.pageCatalog) {
      button.disabled=true;button.setAttribute("aria-busy","true");
      try{await openCatalogAlbum(button.dataset.pageCatalog,button.dataset.pageItem);}
      finally{if(button.isConnected){button.disabled=false;button.removeAttribute("aria-busy");}}
    }
    else if(button.dataset.findAlbum){
      event.preventDefault();const request=state.request,serial=++pagesState.openSerial;
      const catalog=await pageData(`artist:${button.dataset.albumArtist}`,()=>browseCatalog('/api/catalog/search',{type:"artist",query:button.dataset.albumArtist}));
      if(request!==state.request || serial!==pagesState.openSerial)return;
      const item=catalog.items.find(row=>foldName(row.title)===foldName(button.dataset.findAlbum));
      if(item)await openCatalogAlbum(catalog.id,item.id);
      else await openPage("artist",{name:button.dataset.albumArtist,scope:"global"});
    }
    else if(button.dataset.pageVersion){pagesState.version=button.dataset.pageVersion;applyPageVersions();}
    else if(button.dataset.searchAll!==undefined) await searchLibrary(button.dataset.searchAll);
    else if(button.dataset.action==="page-back") await goBackPage();
    else if(button.dataset.action==="page-retry"){pagesState.cache.clear();await render();}
    else if(button.dataset.action==="page-shuffle") await shuffleAll();
  } catch(error){if(pageRequest===state.request)toast(error.message);}
});
