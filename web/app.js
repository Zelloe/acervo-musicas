"use strict";

if(typeof tr!=="function")window.tr=message=>String(message??"");
if(typeof uiLocale!=="function")window.uiLocale=()=>"en-US";

const $ = (selector) => document.querySelector(selector);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const join = (values) => (values || []).join(" / ");
const number = (value) => new Intl.NumberFormat(uiLocale()).format(value || 0);
const duration = (value) => { const seconds = Math.max(0, Math.floor(Number(value) || 0)); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; };
const cover = (id) => `/api/cover?id=${encodeURIComponent(id)}`;
const state = {view:"home", summary:null, serverIncompatible:false, albums:[], proposals:null, pending:[], selected:new Set(), page:0, filter:"all", versionFilter:"all", reviewFilter:"all", albumFilter:"all", search:"", searchArtist:"", searchAlbum:"", searchLabel:"", queue:[], current:null, request:0, lastJob:null};
const token = $('meta[name="library-token"]').content;
const playlistState = {draft:null, pending:null, exists:false};
let toastTimeout;

function catalogPhoto(url, artist=false) {
  return typeof remotePhoto==="function"?remotePhoto(url,artist):`<span class="remote-photo${artist?" artist-photo":""}">${icon(artist?"music":"disc")}</span>`;
}

function recordingBadges(version) {
  return typeof versionBadges==="function"?versionBadges(version):"";
}

function recordingChips(selected, attribute) {
  return typeof versionChips==="function"?versionChips(selected,attribute):"";
}

function recordingMatches(version, choice) {
  return typeof versionMatches==="function"?versionMatches(version,choice):true;
}

function languageHeaders() {
  return {"X-Acervo-Language":typeof uiLanguage==="function"?uiLanguage():"en"};
}

async function api(path, data) {
  const options = data === undefined ? {headers:languageHeaders()} : {method:"POST", headers:{...languageHeaders(), "Content-Type":"application/json", "X-Library-Token":token}, body:JSON.stringify(data)};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || tr("Could not complete the operation."));
  return result;
}

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => { $('#toast').hidden = true; }, 6500);
}

function settings() {
  try {
    const saved = JSON.parse(localStorage.getItem("acervo-options"));
    // Keep the contact saved by versions that used the original preference key.
    const contact = saved?.contact ?? saved?.["contato"];
    return {musicbrainz:saved?.musicbrainz===true, contact:typeof contact==="string"?contact:""};
  }
  catch { return {musicbrainz:false, contact:""}; }
}

function restartMessage() {
  return $('#restart-notice')?.textContent.trim() || tr("Acervo has been updated. Restart the server and reload this page to finish updating.");
}

async function updateState() {
  state.summary = await api("/api/state");
  const info = state.summary;
  const incompatible=!info?.summary || typeof info.summary!=="object" || typeof info.library!=="string" || info.lyrics_tools!==true;
  state.serverIncompatible=incompatible;
  const restart=$('#restart-notice');
  if(restart)restart.hidden=!(incompatible || info?.restart_required || info?.["reinicio_necessario"] || typeof remotePhoto!=="function" || typeof versionChips!=="function" || typeof i18nState==="undefined" || typeof renderBulkLyrics!=="function" || typeof startManualSync!=="function");
  if(incompatible){
    setWriteControls();
    throw new Error(restartMessage());
  }
  $('#library-count').textContent = number(info.summary.read_count);
  $('#folder-label').textContent = info.library.replace(/^\/home\/[^/]+/, "~");
  $('#folder-label').title = info.library;
  $('#review-dot').hidden = !info.proposal_count;
  const busy = info.job?.status === "running";
  $('#job-banner').hidden = !busy;
  $('#job-label').textContent = info.job?.message || "";
  $('#job-percent').textContent = info.job?.progress === null || info.job?.progress === undefined ? "" : `${info.job.progress}%`;
  $('#job-progress').style.width = `${info.job?.progress || 0}%`;
  setWriteControls();
}

function setWriteControls() {
  const blocked = state.serverIncompatible || state.summary?.job?.status === "running" || !!state.summary?.lyrics_busy || !!state.summary?.protection_error;
  $('#top-refresh').disabled = blocked;
  document.querySelectorAll('[data-action="analyze"], [data-action="confirm-apply"], [data-action="confirm-restore"], [data-action="fetch-downloads"], [data-action="review-import"], [data-action="confirm-import"], [data-action="export-playlist"], [data-action="confirm-playlist"], [data-playlist-album], [data-track-protection], [data-protection-album], [data-action="protect-album"], [data-action="unprotect-album"], [data-action="preview-lyrics"], [data-action="confirm-lyrics"], [data-action="bulk-review"], [data-action="bulk-confirm"], [data-action="sync-review"], #lyrics-editor, #playlist-form [type="submit"], #download-form [type="submit"], #simple-download-form [type="submit"], [data-simple-open], [data-action="simple-download"], .simple-track-check, [data-simple-mode], [data-action="simple-select-new"], [data-action="simple-select-all"], [data-action="simple-clear"], [data-action="simple-back"], #simple-download-form input, #simple-download-form select').forEach(el => { el.disabled = blocked; });
  if(typeof updateBulkSelection==="function")updateBulkSelection();
  if(typeof updateSyncControls==="function")updateSyncControls();
}

function featuredAlbums() {
  return [...state.albums].sort((a,b)=>b.tracks-a.tracks || a.album.localeCompare(b.album)).slice(0,6);
}

function albumCard(album) {
  return `<article class="album-card"><div class="album-image"><img src="${cover(album.track_id)}" alt="${tr("Cover of")} ${escapeHTML(album.album)}" loading="lazy"><button class="album-play" data-play="${album.track_id}" aria-label="${tr("Listen")} ${escapeHTML(album.album)}">${icon("play")}</button></div><button class="album-title" data-album="${escapeHTML(album.album)}" title="${escapeHTML(album.album)}">${escapeHTML(album.album)}</button><span class="album-artist">${escapeHTML(album.artist)}</span><span class="album-meta">${escapeHTML(album.year.slice(0,4) || tr("Year unknown"))} · ${album.tracks} ${album.tracks === 1 ? tr("track") : tr("tracks")}</span><button class="text-button" data-playlist-album="${album.track_id}" aria-label="${tr("Export M3U8 for")} ${escapeHTML(album.album)} ${tr("by")} ${escapeHTML(album.artist)}" ${state.summary.job?.status==="running"?"disabled":""}>${icon("download")} ${tr("Export M3U8")}</button><button class="text-button" data-protection-album="${album.track_id}" aria-label="${tr("Protect fields for")} ${escapeHTML(album.album)} ${tr("by")} ${escapeHTML(album.artist)}" ${state.summary.job?.status==="running"?"disabled":""}>${icon("lock")} ${tr("Protect fields")}</button></article>`;
}

function statCard(label, value, name, description, amber = false) {
  return `<div class="stat-card${amber ? " amber" : ""}"><div class="stat-head"><span class="stat-label">${label}</span><span class="stat-icon">${icon(name)}</span></div><strong class="stat-value">${number(value)}</strong><div class="stat-bottom">${description}</div></div>`;
}

function trackTable(tracks, offset = 0) {
  return `<div class="table-wrap"><table><thead><tr><th class="num">#</th><th>${tr("TITLE")}</th><th>${tr("ALBUM")}</th><th>${tr("GENRE")}</th><th>${tr("FORMAT")}</th><th class="time-cell">${icon("clock")}</th><th></th></tr></thead><tbody>${tracks.map((t,i) => `<tr><td class="num row-number"><span>${offset+i+1}</span><button class="icon-button row-play" data-play="${t.id}" aria-label="${tr("Listen")} ${escapeHTML(join(t.title))}">${icon("play")}</button></td><td><div class="track-info"><img class="track-cover" src="${cover(t.id)}" alt="" loading="lazy"><div class="track-text"><span class="track-name" title="${escapeHTML(join(t.title))}">${escapeHTML(join(t.title))}</span><span class="track-artist">${escapeHTML(join(t.artist))}</span>${t.version?recordingBadges(t.version):""}</div></div></td><td class="album-cell" title="${escapeHTML(join(t.album))}">${escapeHTML(join(t.album))}</td><td>${t.genre.length ? `<span class="source">${escapeHTML(join(t.genre))}</span>` : `<span class="missing">${tr("Not set")}</span>`}</td><td><span class="format ${t.format === "FLAC" ? "flac" : ""}">${t.format}</span></td><td class="time-cell">${duration(t.duration_seconds)}</td><td><button class="icon-button" data-info="${t.id}" aria-label="${tr("View metadata for")} ${escapeHTML(join(t.title))}">${icon("more")}</button></td></tr>`).join("")}</tbody></table></div>`;
}

function breadcrumb(label) { return `<div class="page-topline">${tr("Your space")} ${icon("arrow")} <span>${label}</span></div>`; }
function empty(title, text, name="library", action="") { return `<div class="empty-state">${icon(name)}<h2>${title}</h2><p>${text}</p>${action}</div>`; }
function heading(kicker,title,text,actions="") { return `${breadcrumb(title)}<div class="page-heading"><div><span class="section-kicker">${kicker}</span><h1>${title}</h1><p>${text}</p></div><div class="page-heading-actions">${actions}</div></div>`; }

async function renderHome() {
  const result = await api("/api/library?limit=8");
  state.queue = result.tracks.map(t=>t.id);
  const data = state.summary;
  const sum = data.summary;
  const albums = featuredAlbums();
  return `${breadcrumb(tr("Overview"))}<section class="hero"><div class="hero-copy"><div class="eyebrow">${icon("disc")} ${tr("YOUR PERSONAL LIBRARY")}</div><h1>${tr("Your music.")}<br><span>${tr("Every detail matters.")}</span></h1><p>${tr("A collection with your identity.")}<br>${tr("Find what's missing, review the suggestions and bring everything together.")}</p><div class="hero-actions"><button class="button primary" data-view="review">${tr("Review my collection")} ${icon("arrow")}</button><button class="button secondary" data-view="library">${tr("View library")}</button></div></div><div class="hero-art" aria-hidden="true">${albums.map(a=>`<img src="${cover(a.track_id)}" alt="">`).join("")}</div></section><div class="stat-grid">${statCard(tr("Tracks in your collection"),sum.read_count,"music",`${number(sum.formats?.MP3)} MP3 <span class="green">·</span> ${number(sum.formats?.FLAC)} FLAC`)}${statCard(tr("Albums in your library"),data.album_count,"disc",tr("Stories worth listening to"))}${statCard(tr("Missing genres"),sum.missing_fields?.genre,"tag",tr("A collection ready for the finishing touches"),true)}${statCard(tr("Tracks with covers"),sum.with_cover,"image",`<span class="green">${tr("The visual identity of your music")}</span>`)}</div><section class="section"><div class="section-heading"><div><h2>${tr("On your shelf")}</h2><p>${tr("Some of the records that make your collection yours.")}</p></div><button class="text-button" data-view="albums">${tr("View all")} ${icon("arrow")}</button></div><div class="album-grid">${albums.map(albumCard).join("")}</div></section><section class="section overview-table"><div class="section-heading"><div><h2>${tr("Tracks in your collection")}</h2><p>${number(sum.read_count)} ${tr("good reasons to press play.")}</p></div><button class="text-button" data-view="library">${tr("Explore library")} ${icon("arrow")}</button></div>${result.tracks.length ? trackTable(result.tracks) : empty(tr("Your library is being prepared"), tr("The first scan may take a few minutes. Follow its progress at the top of the page."))}</section>`;
}

async function renderLibrary() {
  const query = new URLSearchParams({q:state.search, artist:state.searchArtist, album_id:state.searchAlbum, filter:state.filter, version:state.versionFilter, offset:state.page*50, limit:50});
  const result = await api(`/api/search?${query}`);
  state.queue = result.tracks.map(t=>t.id);
  const searching = !!(state.search || state.searchArtist || state.searchAlbum);
  const description = searching ? `${number(result.total)} ${tr("tracks for “")}${escapeHTML(state.searchLabel || state.search)}${tr("” · results ranked by relevance")}` : `${number(result.total)} ${tr("tracks ·")} ${number(state.summary.artist_count)} ${tr("artist credits")}`;
  return `${heading(tr("YOUR MUSIC, YOUR COLLECTION"),searching?tr("Search results"):tr("All your tracks"),description, `<button class="button ghost" data-view="lyrics">${icon("lyrics")} ${tr("Lyrics for all tracks")}</button><button class="button ghost" data-action="export-playlist" ${state.summary.job?.status==="running"?"disabled":""}>${icon("download")} ${tr("Export M3U8")}</button><button class="button ghost" data-action="analyze">${icon("refresh")} ${tr("Refresh library")}</button>`)}<div class="table-toolbar"><div class="tabs">${[["all",tr("All tracks")],["missing-genre",tr("Genre missing")],["flac","FLAC"],["versions",tr("Versions and extras")]].map(([key,label])=>`<button class="tab ${state.filter===key?"active":""}" data-filter="${key}">${label}</button>`).join("")}</div><span class="table-note">${icon("shield")} ${tr("Your original files")}</span></div>${recordingChips(state.versionFilter,"data-library-version")}${result.tracks.length ? trackTable(result.tracks,state.page*50) : empty(tr("No tracks here"), tr("Try another search or change the filters."), "search")}<div class="table-footer"><span>${result.total ? `${number(state.page*50+1)}–${number(Math.min((state.page+1)*50,result.total))} ${tr("of")} ${number(result.total)} ${tr("tracks")}` : tr("0 tracks")}</span><div class="pagination"><button class="button small ghost" data-page="${state.page-1}" ${state.page===0?"disabled":""}>${tr("Previous")}</button><button class="button small ghost" data-page="${state.page+1}" ${(state.page+1)*50>=result.total?"disabled":""}>${tr("Next")}</button></div></div>`;
}

function filteredProposals() {
  const needle = state.search.toLocaleLowerCase("pt-BR");
  return (state.proposals||[]).filter(p=>(state.reviewFilter==="all" || p.field===state.reviewFilter) && (!needle || `${p.title} ${p.artist} ${p.album} ${join(p.proposed)}`.toLocaleLowerCase("pt-BR").includes(needle)));
}

async function renderReview() {
  if (state.proposals === null) {
    const [proposals, pending] = await Promise.all([api("/api/proposals"),api("/api/unidentified")]);
    state.proposals = proposals.changes;
    state.pending = pending.tracks;
  }
  const reviewTabs = () => `<div class="tabs">${[["all",tr("All")],["genre",tr("Genres")],["album",tr("Albums")],["pendentes",`${tr("No suggestion ·")} ${number(state.pending.length)}`]].map(([key,label])=>`<button class="tab ${state.reviewFilter===key?"active":""}" data-review-filter="${key}">${label}</button>`).join("")}</div>`;
  if (state.reviewFilter === "pendentes") {
    const query = state.search.toLowerCase();
    const rows = state.pending.filter(t=>`${join(t.title)} ${join(t.artist)} ${join(t.album)}`.toLowerCase().includes(query));
    return `${heading(tr("A CLOSER LOOK"),tr("A few tracks need a closer look."),`${number(rows.length)} ${tr("tracks without a reliable genre suggestion.")}`)}<div class="review-summary">${icon("search")}<p><strong>${tr("A little doubt is better than a wrong tag.")}</strong>${tr("These tracks were preserved. You can enable MusicBrainz and scan again.")}</p><button class="button small ghost" data-action="preferences">${tr("Preferences")}</button></div>${reviewTabs()}${rows.length?trackTable(rows):empty(tr("No pending tracks in this filter"),tr("Try another search or check the available suggestions."),"check")}`;
  }
  const rows = filteredProposals();
  const visible = rows.slice(state.page*40,(state.page+1)*40);
  state.visibleProposals = visible;
  const counts = state.summary.summary.proposals_by_field || {};
  const titles = {genre:tr("Genre"), album:tr("Album")};
  return `${heading(tr("YOU CHOOSE WHAT CHANGES"),tr("A little more harmony."),`${number(counts.genre)} ${tr("genre suggestions and")} ${number(counts.album)} ${tr("album suggestions, ready for review.")}`, `<a class="button ghost" href="/api/export">${icon("download")} ${tr("Export preview")}</a>`)}<div class="review-summary">${icon("shield")}<p><strong>${tr("Your collection stays in your hands.")}</strong>${tr("Select the changes that make sense. Each operation includes a full backup.")}</p><span class="chip">${tr("Approve each change")}</span></div><div class="table-toolbar">${reviewTabs()}${rows.length ? `<button class="text-button" data-action="select-filtered">${tr("Select")} ${number(rows.length)} ${tr("filtered suggestions")}</button>` : ""}</div>${rows.length ? `<div class="table-wrap"><table class="review-table"><thead><tr><th><input class="check" id="check-visible" type="checkbox" aria-label="${tr("Select changes on this page")}"></th><th>${tr("TRACK")}</th><th>${tr("FIELD")}</th><th>${tr("CURRENT → SUGGESTED")}</th><th>${tr("SOURCE")}</th></tr></thead><tbody>${visible.map(p=>`<tr><td><input class="check proposal-check" type="checkbox" data-id="${p.id}" aria-label="${tr("Approve")} ${titles[p.field]} ${tr("of")} ${escapeHTML(p.title)}" ${state.selected.has(p.id)?"checked":""}></td><td><div class="track-info"><img class="track-cover" src="${cover(p.track_id)}" alt="" loading="lazy"><div class="track-text"><span class="track-name">${escapeHTML(p.title)}</span><span class="track-artist">${escapeHTML(p.artist)}</span></div></div></td><td><span class="chip ${p.field==="album"?"amber":"neutral"}">${titles[p.field]}</span></td><td><span class="change-old">${escapeHTML(join(p.current) || tr("Not set"))}</span><span class="change-new">${icon("arrow")}${escapeHTML(join(p.proposed))}</span>${p.field==="album"?`<span class="source">${tr("Previous edition preserved in a separate tag")}</span>`:""}</td><td><span class="source ${p.source.includes("usuario")?"user":""}" title="${escapeHTML(p.criterion)}">${p.source.startsWith("https://musicbrainz.org/")?tr("MusicBrainz · review"):p.source.includes("usuario")?tr("Your genre rule"):tr("Curated suggestion")}</span></td></tr>`).join("")}</tbody></table></div><div class="table-footer"><span>${number(state.page*40+1)}–${number(Math.min((state.page+1)*40,rows.length))} ${tr("of")} ${number(rows.length)} ${tr("changes · nothing applied")}</span><div class="pagination"><button class="button small ghost" data-page="${state.page-1}" ${state.page===0?"disabled":""}>${tr("Previous")}</button><button class="button small ghost" data-page="${state.page+1}" ${(state.page+1)*40>=rows.length?"disabled":""}>${tr("Next")}</button></div></div>` : empty(tr("All reviewed here"), tr("No suggestions for this filter. You can also check tracks without suggestions."), "check")}<div id="selection-bar"></div>`;
}

function updateSelection() {
  const target = $('#selection-bar');
  if (target) {
    target.className = state.selected.size ? "selection-bar" : "";
    target.innerHTML = state.selected.size ? `<span><strong>${number(state.selected.size)}</strong> ${tr("changes selected")}</span><div class="tabs"><button class="text-button" data-action="clear-selection">${tr("Clear selection")}</button><button class="button primary" data-action="review-apply">${tr("Review and apply")} ${icon("arrow")}</button></div>` : "";
  }
  const check = $('#check-visible');
  if (check) {
    const count = (state.visibleProposals||[]).filter(p=>state.selected.has(p.id)).length;
    check.checked = count>0 && count===state.visibleProposals.length;
    check.indeterminate = count>0 && count<state.visibleProposals.length;
  }
  document.querySelectorAll('.proposal-check').forEach(el=>{ el.checked=state.selected.has(el.dataset.id); });
}

async function renderAlbums() {
  let rows = state.albums;
  if (state.albumFilter === "editions") {
    const key = a => `${a.artist.toLowerCase()}|${a.album.normalize("NFKC").replace(/\s*[([][^()[\]]*(?:remaster|deluxe|expanded|anniversary)[^()[\]]*[)\]]\s*$/i, "").trim().toLowerCase()}`;
    const groups = new Map();
    for (const a of rows) groups.set(key(a),(groups.get(key(a))||0)+1);
    rows=rows.filter(a=>groups.get(key(a))>1);
  }
  const needle = state.search.toLowerCase();
  if (needle) rows = rows.filter(a=>`${a.album} ${a.artist}`.toLowerCase().includes(needle));
  return `${heading(tr("YOUR RECORD SHELF, DUST FREE"),tr("Records that tell stories."),`${number(rows.length)} ${tr("albums · editions shown as they appear in your files")}`)}<div class="table-toolbar"><div class="tabs"><button class="tab ${state.albumFilter==="all"?"active":""}" data-album-filter="all">${tr("All albums")}</button><button class="tab ${state.albumFilter==="editions"?"active":""}" data-album-filter="editions">${tr("Similar names")}</button></div><span class="table-note">${icon("disc")} ${tr("Every edition has a story")}</span></div>${rows.length ? `<div class="album-grid full">${rows.map(albumCard).join("")}</div>` : empty(tr("No albums found"), tr("Try another search or filter."), "disc")}`;
}

async function renderHistory() {
  const rows = (await api("/api/history")).runs;
  const labels = {completed:tr("Completed"),restored:tr("Restored"),interrupted:tr("Interrupted"),applying:tr("In progress"),preparing_backups:tr("Preparing backup")};
  return `${heading(tr("EVERY CHANGE HAS A WAY BACK"),tr("Collection history"), tr("Review your changes and restore files from an earlier operation."))}${rows.length ? rows.map(r=>`<article class="history-row"><span class="history-icon">${icon("history")}</span><div class="history-text"><strong>${number(r.files)} ${r.files===1?tr("file"):tr("files")} · ${r.category==="lyrics"?tr(r.files>1?"lyrics batch":"track lyrics"):r.type==="downloads"?tr("download import"):tr("metadata update")}</strong><p>${escapeHTML(new Date(r.date).toLocaleString(uiLocale()))} · ${r.type==="downloads"?tr("originals backed up · additions can be archived when undoing"):tr("full backup")}${r.error?" · "+escapeHTML(r.error):""}</p></div><span class="chip ${r.status==="interrupted"?"amber":""}">${escapeHTML(labels[r.status]||r.status)}</span><button class="button small ghost" data-restore="${r.id}" data-restore-kind="${r.category==="lyrics"?"metadata":r.type==="downloads"?"downloads":"metadata"}" ${r.status==="restored"?"disabled":""}>${icon("history")} ${r.category==="lyrics"?tr("Restore lyrics"):r.type==="downloads"?tr("Undo import"):tr("Restore")}</button></article>`).join("") : empty(tr("Your collection starts a new chapter."), tr("Backups and records will appear here when you apply your first changes. Until then, your files stay as they are."), "history", `<button class="button primary" data-view="review">${tr("Explore suggestions")} `+icon("arrow")+'</button>')}`;
}

async function render() {
  const request = ++state.request;
  document.querySelectorAll('.nav-item').forEach(el=>{ el.classList.toggle("active",el.dataset.view===(state.view==="lyrics"?"library":state.view)); el.setAttribute("aria-current",el.dataset.view===(state.view==="lyrics"?"library":state.view)?"page":"false"); });
  if(state.serverIncompatible){
    $('#content').innerHTML=empty(tr("Could not open the library"),escapeHTML(restartMessage()),"folder");
    setWriteControls();
    return;
  }
  try {
    let html = await ({home:renderHome,library:renderLibrary,lyrics:typeof renderBulkLyrics==="function"?renderBulkLyrics:()=>empty(tr("Could not open the library"),escapeHTML(restartMessage()),"lyrics"),downloads:renderSimpleDownloads,"advanced-downloads":renderDownloads,review:renderReview,albums:renderAlbums,history:renderHistory}[state.view] || renderHome)();
    if (request !== state.request) return;
    if (state.summary.error) html = `<div class="error-box">${escapeHTML(state.summary.error)}</div>`+html;
    if (state.summary.protection_error) html = `<div class="error-box"><strong>${tr("Protected fields unavailable.")}</strong> ${escapeHTML(state.summary.protection_error)} ${tr("Changes are blocked until this file is fixed.")}</div>`+html;
    $('#content').innerHTML=html;
    setWriteControls();
    applySimpleVersionFilter();
    updateSelection();
  } catch(error) { toast(error.message); }
}

function navigate(view) {
  if (!["home","library","lyrics","downloads","advanced-downloads","review","albums","history"].includes(view)) view="home";
  state.view=view;
  state.page=0;
  history.replaceState(null,"",`#${view}`);
  window.scrollTo({top:0});
  return render();
}

function modal(content) { $('#modal-content').innerHTML=content; setWriteControls(); if (!$('#modal').open) $('#modal').showModal(); }
function modalHead(title,text) { return `<div class="modal-head"><div><h2>${title}</h2><p>${text}</p></div><button class="icon-button" data-action="close-modal" aria-label="${tr("Close")}">${icon("close")}</button></div>`; }
function bytes(value) {
  const gigabytes=value>=1073741824;
  const digits=gigabytes?2:1;
  const amount=new Intl.NumberFormat(uiLocale(),{minimumFractionDigits:digits,maximumFractionDigits:digits}).format(value/(gigabytes?1073741824:1048576));
  return `${amount} ${gigabytes?"GB":"MB"}`;
}

function showPlaylist(albumId="", previousName) {
  const album = state.albums.find(a=>a.track_id===albumId);
  playlistState.draft = albumId ? {album_id:albumId} : {q:state.search, filter:state.filter, artist:state.searchArtist, album_id:state.searchAlbum, version:state.versionFilter};
  playlistState.pending = null;
  const name = (previousName ?? (album?.album || state.search || tr("My selection"))).replace(/[\\/:*?"<>|\x00-\x1f\x7f]/g,"_").slice(0,90);
  const scope = album ? `${tr("Album:")} ${album.album} · ${album.artist}` : `${tr("All tracks matching the current search and filters, including other pages")}${state.search?` ${tr("· search:")} ${state.search}`:""}.`;
  modal(`${modalHead(tr("Export M3U8 playlist"),escapeHTML(scope))}<form id="playlist-form"><div class="modal-body"><label for="playlist-name">${tr("Playlist name")}</label><input class="field" id="playlist-name" name="name" required maxlength="100" value="${escapeHTML(name)}"><div class="backup-note">${icon("folder")}<span>${tr("The playlist will be saved in")} <strong>Playlists/</strong> ${tr("in your collection, using relative paths. To use another drive, copy your collection together with this folder.")}</span></div></div><div class="modal-actions"><button class="button ghost" type="button" data-action="close-modal">${tr("Cancel")}</button><button class="button primary" type="submit">${tr("Preview")} ${icon("arrow")}</button></div></form>`);
  $('#playlist-name').focus();
}

document.addEventListener("submit",async event=>{
  if(event.target.id!=="playlist-form")return;
  event.preventDefault();
  const submit=event.target.querySelector('[type="submit"]');
  submit.disabled=true;
  try {
    const data={...playlistState.draft,name:$('#playlist-name').value};
    const result=await api("/api/playlists/preview",data);
    playlistState.pending={...data,previous_sha256:result.previous_sha256};
    playlistState.exists=result.exists;
    modal(`${modalHead(tr("Review playlist"),`${number(result.tracks)} ${tr("tracks ordered by artist, album, disc and track number.")}`)}<div class="modal-body"><dl class="details-list"><div><dt>${tr("Destination")}</dt><dd>${escapeHTML(result.playlist)}</dd></div><div><dt>${tr("Format")}</dt><dd>${tr("M3U8 · UTF-8 · relative paths")}</dd></div></dl>${result.exists?`<div class="form-row"><label><input class="check" id="playlist-overwrite" type="checkbox"> ${tr("Replace the existing playlist")}</label><p>${tr("The previous version will be backed up. To keep both in your collection, go back and choose another name.")}</p></div>`:""}<ul class="confirm-list">${result.sample.map(t=>`<li><strong>${escapeHTML(join(t.title))}</strong><span>${escapeHTML(join(t.artist))}</span></li>`).join("")}</ul>${result.tracks>result.sample.length?`<p class="download-hint">${tr("And")} ${number(result.tracks-result.sample.length)} ${tr("more tracks in the selection.")}</p>`:""}</div><div class="modal-actions"><button class="button ghost" data-action="edit-playlist">${tr("Change name")}</button><button class="button primary" data-action="confirm-playlist">${icon("download")} ${tr("Save playlist")}</button></div>`);
  }catch(error){submit.disabled=false;toast(error.message);}
});

function confirmApply() {
  const selected = state.proposals.filter(p=>state.selected.has(p.id));
  if (!selected.length) return;
  const files = new Map(selected.map(p=>[p.file,p.size_bytes]));
  const size = [...files.values()].reduce((a,b)=>a+b,0);
  modal(`${modalHead(tr("Ready to refine these details?"),`${number(selected.length)} ${tr("changes across")} ${number(files.size)} ${tr("files. Review your selection before continuing.")}`)}<div class="modal-body"><div class="backup-note">${icon("shield")}<span>${tr("A full backup will be created:")} <strong>${bytes(size)}</strong> ${tr("in")} <strong>backups/</strong>${tr(". You can then restore this operation from History.")}</span></div><ul class="confirm-list">${selected.map(p=>`<li><strong>${escapeHTML(p.artist)} — ${escapeHTML(p.title)}</strong><span>${p.field==="genre"?tr("Genre"):tr("Album")}: ${escapeHTML(join(p.current)||tr("Not set"))} → ${escapeHTML(join(p.proposed))}</span>${p.field==="album"?`<span>${tr("The previous album name will be preserved in a separate tag.")}</span>`:""}</li>`).join("")}</ul></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Back to review")}</button><button class="button primary" data-action="confirm-apply">${icon("check")} ${tr("Back up and apply")}</button></div>`);
}

async function showDetails(id) {
  const track = await api(`/api/track?id=${encodeURIComponent(id)}`);
  const pairs = [["title",tr("Title"),join(track.title)],["artist",tr("Artist"),join(track.artist)],["album_artist",tr("Album artist"),join(track.album_artist)],["album",tr("Album"),join(track.album)],["genre",tr("Genre"),join(track.genre)],["year",tr("Year"),join(track.year)],["track",tr("Track"),join(track.track)],["disc",tr("Disc"),join(track.disc)],[null,tr("Audio"),`${track.format} · ${duration(track.duration_seconds)} · ${Math.round((track.bitrate_bps||0)/1000)} kbps`],[null,tr("Covers"),`${track.covers.length} ${tr("images preserved")}`],[null,tr("File"),track.file]];
  modal(`${modalHead(tr("Track details"), tr("Metadata read from your file. Protect fields to preserve your choices."))}<div class="detail-hero"><img src="${cover(id)}" alt="${tr("Album cover")}"><div><h3>${escapeHTML(join(track.title))}</h3><p>${escapeHTML(join(track.artist))}</p><button class="button small primary" data-play="${id}">${icon("play")} ${tr("Listen")}</button><button class="button small ghost" data-lyrics="${escapeHTML(id)}">${icon("lyrics")} ${tr("Lyrics")}</button></div></div><div class="modal-body">${track.protection_error?`<div class="error-box">${escapeHTML(track.protection_error)}</div>`:""}<dl class="details-list">${pairs.map(([field,k,v])=>`<div><dt>${k}</dt><dd>${escapeHTML(v||tr("Not set"))}${field?protectionControl(track,field):""}</dd></div>`).join("")}</dl></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Close")}</button></div>`);
}

function showSettings() {
  const options=settings();
  modal(`${modalHead(tr("Make it yours."),tr("Preferences for the next library scan."))}<div class="modal-body"><div class="form-row"><label><input type="checkbox" class="check" id="use-musicbrainz" ${options.musicbrainz?"checked":""}> ${tr("Look up MusicBrainz for genres without a local rule")}</label><p>${tr("Looks up artist and album names. Suggestions always require your approval. No audio files are uploaded.")}</p><input id="musicbrainz-contact" class="field" type="text" placeholder="${tr("Your contact email or URL")}" aria-label="${tr("MusicBrainz client contact")}" value="${escapeHTML(options.contact)}"></div><div class="form-row"><label>${icon("folder")} ${tr("Current library")}</label><p>${escapeHTML(state.summary.library)}</p></div><div class="form-row"><label>${icon("tag")} ${tr("Editable genre rules")}</label><p>${tr("Local suggestions are stored in genre_rules.csv. Genres already present in your files are preserved.")}</p></div><div class="form-row"><label>${icon("lock")} ${tr("Preserved choices")}</label><p><button class="text-button" data-action="protection-list">${tr("View protected fields and unlock")}</button></p></div><div class="form-row"><label>${icon("download")} ${tr("More options")}</label><p><button class="text-button" data-view="lyrics">${tr("Lyrics for all tracks")}</button></p><p><button class="text-button" data-view="advanced-downloads">${tr("Advanced downloads with review and import")}</button></p></div></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Cancel")}</button><button class="button primary" data-action="save-settings">${tr("Save preferences")}</button></div>`);
}

async function analyze() {
  const options=settings();
  if (options.musicbrainz && !options.contact.trim()) { showSettings(); toast(tr("Enter your contact information to identify the MusicBrainz client.")); return; }
  await api("/api/analyze",options);
  await updateState();
  toast(tr("Scan started. Follow its progress at the top."));
}

const audio=$('#audio');
audio.volume=.7;
async function play(id) {
  if (state.current?.id === id) {
    if (audio.paused) await audio.play(); else audio.pause();
    return;
  }
  const track=await api(`/api/track?id=${encodeURIComponent(id)}`);
  state.current=track;
  if(typeof setLyricsTrack==="function")setLyricsTrack(track);
  $('#player-title').dataset.i18nUser="true";$('#player-title').textContent=join(track.title);
  $('#player-artist').dataset.i18nUser="true";$('#player-artist').textContent=join(track.artist);
  $('#player-cover').innerHTML=`<img src="${cover(id)}" alt="">`;
  $('#audio-quality').textContent=`${track.format} · ${Math.round((track.bitrate_bps||0)/1000)} KBPS`;
  audio.src=`/api/audio?id=${encodeURIComponent(id)}`;
  $('#play-toggle').disabled=false;
  $('#seek').disabled=false;
  $('#time-total').textContent=duration(track.duration_seconds);
  const inQueue=state.queue.includes(id);
  $('#previous').disabled=!inQueue || state.queue.indexOf(id)===0;
  $('#next').disabled=!inQueue || state.queue.indexOf(id)===state.queue.length-1;
  await audio.play();
}
audio.addEventListener("play",()=>{ $('#play-toggle').innerHTML=icon("pause"); $('#play-toggle').setAttribute("aria-label",tr("Pause")); $('#playing-dot').hidden=false; });
audio.addEventListener("pause",()=>{ $('#play-toggle').innerHTML=icon("play"); $('#play-toggle').setAttribute("aria-label",tr("Play")); $('#playing-dot').hidden=true; });
audio.addEventListener("timeupdate",()=>{ $('#time-current').textContent=duration(audio.currentTime); $('#seek').value=Number.isFinite(audio.duration)&&audio.duration>0 ? audio.currentTime/audio.duration*1000 : 0; });
audio.addEventListener("loadedmetadata",()=>{ $('#time-total').textContent=duration(audio.duration); });
audio.addEventListener("error",()=>toast(tr("Could not play this file in the browser.")));
audio.addEventListener("ended",()=>{ const next=state.queue[state.queue.indexOf(state.current?.id)+1]; if(next)play(next).catch(e=>toast(e.message)); });
$('#play-toggle').addEventListener("click",()=>{ if (audio.paused)audio.play().catch(e=>toast(e.message));else audio.pause(); });
$('#volume').addEventListener("input",e=>{ audio.volume=Number(e.target.value)/100; });
$('#seek').addEventListener("input",e=>{ if(Number.isFinite(audio.duration))audio.currentTime=Number(e.target.value)/1000*audio.duration; });
for (const [selector,delta] of [["#previous",-1],["#next",1]]) $(selector).addEventListener("click",()=>{ const id=state.queue[state.queue.indexOf(state.current?.id)+delta];if(id)play(id).catch(e=>toast(e.message)); });

document.addEventListener("click",async event=>{
  const button=event.target.closest("button,a");
  if(!button || button.disabled)return;
  try {
    if(button.dataset.view){ if($('#modal').open)$('#modal').close();state.search="";state.searchArtist="";state.searchAlbum="";state.searchLabel="";$('#search').value="";navigate(button.dataset.view); }
    else if(button.dataset.play)await play(button.dataset.play);
    else if(button.dataset.info)await showDetails(button.dataset.info);
    else if(button.dataset.playlistAlbum)showPlaylist(button.dataset.playlistAlbum);
    else if(button.dataset.album){ state.search=button.dataset.album;state.searchArtist="";state.searchAlbum="";state.searchLabel="";$('#search').value=state.search;state.filter="all";navigate("library"); }
    else if(button.dataset.libraryVersion){state.versionFilter=button.dataset.libraryVersion;state.page=0;await render();}
    else if(button.dataset.filter){state.filter=button.dataset.filter;state.page=0;await render();}
    else if(button.dataset.reviewFilter){state.reviewFilter=button.dataset.reviewFilter;state.page=0;await render();}
    else if(button.dataset.albumFilter){state.albumFilter=button.dataset.albumFilter;await render();}
    else if(button.dataset.page!==undefined){state.page=Math.max(0,Number(button.dataset.page));await render();}
    else if(button.dataset.restore){
      const imported=button.dataset.restoreKind==="downloads";
      modal(`${modalHead(imported?tr("Undo this import?"):tr("Restore the previous state?"),imported?tr("Added files will be archived outside your collection. Replacements and tag changes will be restored from backups."):tr("Files from this operation will be restored exactly from the backup."))}<div class="modal-body"><div class="backup-note">${icon("history")}<span>${imported?tr("Later edits to imported files prevent restoration. Entries that were never imported are preserved and may produce notices."):tr("If any file was edited after this operation, restoration will be refused to preserve those changes.")}</span></div></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Cancel")}</button><button class="button primary" data-action="confirm-restore" data-id="${button.dataset.restore}">${imported?tr("Archive additions and restore"):tr("Restore files")}</button></div>`);
    } else {
      const action=button.dataset.action;
      if(action==="close-modal")$('#modal').close();
      else if(action==="export-playlist")showPlaylist();
      else if(action==="edit-playlist")showPlaylist(playlistState.draft?.album_id||"",playlistState.pending?.name);
      else if(action==="confirm-playlist"){
        if(!playlistState.pending)return;
        const overwrite=!!$('#playlist-overwrite')?.checked;
        if(playlistState.exists&&!overwrite)throw new Error(tr("Choose to replace the existing playlist, or go back and change its name."));
        button.disabled=true;
        const result=await api("/api/playlists/export",{...playlistState.pending,overwrite:overwrite});
        playlistState.pending=null;
        modal(`${modalHead(tr("Playlist saved"),`${number(result.found_count)} ${tr("of")} ${number(result.tracks)} ${tr("paths checked.")}`)}<div class="modal-body"><dl class="details-list"><div><dt>${tr("File")}</dt><dd>${escapeHTML(result.playlist)}</dd></div><div><dt>${tr("To move to another drive")}</dt><dd>${tr("Copy your collection together with the Playlists/ folder.")}</dd></div>${result.backup?`<div><dt>${tr("Previous copy")}</dt><dd>${escapeHTML(result.backup)}</dd></div><div><dt>${tr("To restore the playlist")}</dt><dd>${tr("Copy the backup to the playlist path shown above.")}</dd></div>`:""}</dl>${result.missing.length?`<p class="download-warning">${number(result.missing.length)} ${tr("files became unavailable while saving. Check your collection.")}</p>`:""}</div><div class="modal-actions"><button class="button primary" data-action="close-modal">${tr("Done")}</button></div>`);
      }
      else if(action==="preferences")showSettings();
      else if(action==="select-filtered"){filteredProposals().forEach(p=>state.selected.add(p.id));updateSelection();}
      else if(action==="clear-selection"){state.selected.clear();updateSelection();}
      else if(action==="review-apply")confirmApply();
      else if(action==="confirm-apply"){
        button.disabled=true;
        await api("/api/apply",{ids:[...state.selected]});$('#modal').close();await updateState();toast(tr("Operation started. Backups come first."));
      } else if(action==="confirm-restore"){
        button.disabled=true;
        await api("/api/restore",{id:button.dataset.id});$('#modal').close();await updateState();
      } else if(action==="save-settings"){
        const options={musicbrainz:$('#use-musicbrainz').checked,contact:$('#musicbrainz-contact').value.trim()};
        if(options.musicbrainz&&!options.contact){toast(tr("Enter a valid contact email or URL."));$('#musicbrainz-contact').focus();return;}
        localStorage.setItem("acervo-options",JSON.stringify(options));$('#modal').close();toast(tr("Preferences saved for the next scan."));
      } else if(action==="analyze")await analyze();
    }
  } catch(error) { button.disabled=false;toast(error.message); }
});

document.addEventListener("change",event=>{
  if(event.target.matches(".proposal-check")){ if(event.target.checked)state.selected.add(event.target.dataset.id);else state.selected.delete(event.target.dataset.id);updateSelection(); }
  if(event.target.id==="check-visible"){ for(const p of state.visibleProposals){if(event.target.checked)state.selected.add(p.id);else state.selected.delete(p.id);}updateSelection(); }
});
$('#settings').addEventListener("click",showSettings);
$('#top-refresh').addEventListener("click",()=>analyze().catch(e=>toast(e.message)));
document.addEventListener("keydown",event=>{if(event.key==="/"&&!event.target.matches("input,textarea")){event.preventDefault();$('#search').focus();}});
window.addEventListener("hashchange",()=>navigate(location.hash.slice(1)));

document.addEventListener("languagechange",async()=>{
  try{
    simpleFormValues($('#simple-download-form'));
    if($('#download-form'))downloadDraft($('#download-form'));
    $('#play-toggle').setAttribute("aria-label",tr(audio.paused?"Play":"Pause"));
    if(state.current?.id?.startsWith("stage-"))$('#player-artist').textContent=join(state.current.artist)+" "+tr("· download preview");
    const scope=typeof searchState!=="undefined"?searchState.scope:"library";
    const label=tr(scope==="global"?"Search tracks or artists in the global catalog":"Search tracks, artists or albums");
    $('#search').placeholder=label;$('#search').setAttribute("aria-label",label);
    if(typeof refreshLyricsLanguage==="function")refreshLyricsLanguage();
    await render();
  }catch(error){toast(error.message);}
});

async function poll() {
  try {
    const previous=state.summary?.analyzed_at;
    const previousLibrary=state.summary?.library_revision;
    const previousPolicy=state.summary?.protection_revision;
    const previousPolicyError=state.summary?.protection_error;
    await updateState();
    const job=state.summary.job;
    const jobKey=job?`${job.id}:${job.status}`:"";
    if(job?.status==="error"&&state.lastJob!==jobKey)toast(job.message);
    if(typeof captureLyricsJob==="function")await captureLyricsJob(job);
    const bulkChanged=typeof captureBulkLyricsJob==="function"?await captureBulkLyricsJob(job):false;
    const simpleChanged=captureSimpleDownloadJob(job);
    if(state.view==="downloads"&&simpleChanged)await render();
    if(job?.type==="direct_download"&&state.lastJob!==jobKey&&job.status==="completed"&&job.result?.warnings?.length)toast(job.result.warnings.join(" "));
    if(state.view==="advanced-downloads"&&state.lastJob!==jobKey&&job?.status!=="running"){
      if(job?.type==="catalog"&&job.result?.batch)await openDownloadBatch(job.result.batch);
      else await render();
    }
    const finishedLibraryJob=job&&state.lastJob!==jobKey&&["completed","error"].includes(job.status)&&["analysis","apply","restore","import"].includes(job.type);
    if(finishedLibraryJob || previous!==state.summary.analyzed_at || previousLibrary!==state.summary.library_revision || previousPolicy!==state.summary.protection_revision || previousPolicyError!==state.summary.protection_error){state.proposals=null;state.selected.clear();state.albums=(await api("/api/albums")).albums;await render();}
    else if(state.view==="lyrics"&&bulkChanged)await render();
    state.lastJob=jobKey;
  } catch(error){if(!$('#toast').hidden)return;toast(tr("Connection lost. Check that the app is still running."));}
  finally {setTimeout(poll,2000);}
}

(async()=>{
  try {
    await updateState();
    state.albums=(await api("/api/albums")).albums;
    navigate(location.hash.slice(1)||"home");
    poll();
  }catch(error){$('#content').innerHTML=empty(tr("Could not open the library"),escapeHTML(error.message),"folder");}
})();
