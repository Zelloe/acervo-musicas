"use strict";

if(typeof tr!=="function")window.tr=message=>String(message??"");
if(typeof uiLocale!=="function")window.uiLocale=()=>"pt-BR";

const $ = (selector) => document.querySelector(selector);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const join = (values) => (values || []).join(" / ");
const number = (value) => new Intl.NumberFormat(uiLocale()).format(value || 0);
const duration = (value) => { const seconds = Math.max(0, Math.floor(Number(value) || 0)); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; };
const cover = (id) => `/api/cover?id=${encodeURIComponent(id)}`;
const state = {view:"inicio", summary:null, albums:[], proposals:null, pending:[], selected:new Set(), page:0, filter:"todas", versionFilter:"todas", reviewFilter:"todas", albumFilter:"todas", search:"", searchArtist:"", searchAlbum:"", searchLabel:"", queue:[], current:null, request:0, lastJob:null};
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

async function api(path, data) {
  const options = data === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json", "X-Library-Token":token}, body:JSON.stringify(data)};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.erro || tr("Não foi possível concluir a operação."));
  return result;
}

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => { $('#toast').hidden = true; }, 6500);
}

function settings() {
  try { return JSON.parse(localStorage.getItem("acervo-options")) || {musicbrainz:false, contato:""}; }
  catch { return {musicbrainz:false, contato:""}; }
}

async function updateState() {
  state.summary = await api("/api/state");
  const info = state.summary;
  const restart=$('#restart-notice');
  if(restart)restart.hidden=!(info.reinicio_necessario || typeof remotePhoto!=="function" || typeof versionChips!=="function" || typeof i18nState==="undefined");
  $('#library-count').textContent = number(info.resumo.lidos);
  $('#folder-label').textContent = info.biblioteca.replace(/^\/home\/[^/]+/, "~");
  $('#folder-label').title = info.biblioteca;
  $('#review-dot').hidden = !info.propostas;
  const busy = info.job?.estado === "executando";
  $('#job-banner').hidden = !busy;
  $('#job-label').textContent = info.job?.mensagem || "";
  $('#job-percent').textContent = info.job?.progresso === null || info.job?.progresso === undefined ? "" : `${info.job.progresso}%`;
  $('#job-progress').style.width = `${info.job?.progresso || 0}%`;
  setWriteControls();
}

function setWriteControls() {
  const blocked = state.summary?.job?.estado === "executando" || !!state.summary?.erro_protecoes;
  $('#top-refresh').disabled = blocked;
  document.querySelectorAll('[data-action="analyze"], [data-action="confirm-apply"], [data-action="confirm-restore"], [data-action="fetch-downloads"], [data-action="review-import"], [data-action="confirm-import"], [data-action="export-playlist"], [data-action="confirm-playlist"], [data-playlist-album], [data-track-protection], [data-protection-album], [data-action="protect-album"], [data-action="unprotect-album"], [data-action="preview-lyrics"], [data-action="confirm-lyrics"], #lyrics-editor, #playlist-form [type="submit"], #download-form [type="submit"], #simple-download-form [type="submit"], [data-simple-open], [data-action="simple-download"], .simple-track-check, [data-simple-mode], [data-action="simple-select-new"], [data-action="simple-select-all"], [data-action="simple-clear"], [data-action="simple-back"], #simple-download-form input, #simple-download-form select').forEach(el => { el.disabled = blocked; });
}

function featuredAlbums() {
  return [...state.albums].sort((a,b)=>b.faixas-a.faixas || a.album.localeCompare(b.album)).slice(0,6);
}

function albumCard(album) {
  return `<article class="album-card"><div class="album-image"><img src="${cover(album.track_id)}" alt="${tr("Capa de")} ${escapeHTML(album.album)}" loading="lazy"><button class="album-play" data-play="${album.track_id}" aria-label="${tr("Ouvir")} ${escapeHTML(album.album)}">${icon("play")}</button></div><button class="album-title" data-album="${escapeHTML(album.album)}" title="${escapeHTML(album.album)}">${escapeHTML(album.album)}</button><span class="album-artist">${escapeHTML(album.artista)}</span><span class="album-meta">${escapeHTML(album.ano.slice(0,4) || tr("Sem ano"))} · ${album.faixas} ${album.faixas === 1 ? tr("faixa") : tr("faixas")}</span><button class="text-button" data-playlist-album="${album.track_id}" aria-label="${tr("Exportar M3U8 de")} ${escapeHTML(album.album)} ${tr("por")} ${escapeHTML(album.artista)}" ${state.summary.job?.estado==="executando"?"disabled":""}>${icon("download")} ${tr("Exportar M3U8")}</button><button class="text-button" data-protection-album="${album.track_id}" aria-label="${tr("Proteger campos de")} ${escapeHTML(album.album)} ${tr("por")} ${escapeHTML(album.artista)}" ${state.summary.job?.estado==="executando"?"disabled":""}>${icon("lock")} ${tr("Proteger campos")}</button></article>`;
}

function statCard(label, value, name, description, amber = false) {
  return `<div class="stat-card${amber ? " amber" : ""}"><div class="stat-head"><span class="stat-label">${label}</span><span class="stat-icon">${icon(name)}</span></div><strong class="stat-value">${number(value)}</strong><div class="stat-bottom">${description}</div></div>`;
}

function trackTable(tracks, offset = 0) {
  return `<div class="table-wrap"><table><thead><tr><th class="num">#</th><th>${tr("TÍTULO")}</th><th>${tr("ÁLBUM")}</th><th>${tr("GÊNERO")}</th><th>${tr("FORMATO")}</th><th class="time-cell">${icon("clock")}</th><th></th></tr></thead><tbody>${tracks.map((t,i) => `<tr><td class="num row-number"><span>${offset+i+1}</span><button class="icon-button row-play" data-play="${t.id}" aria-label="${tr("Ouvir")} ${escapeHTML(join(t.titulo))}">${icon("play")}</button></td><td><div class="track-info"><img class="track-cover" src="${cover(t.id)}" alt="" loading="lazy"><div class="track-text"><span class="track-name" title="${escapeHTML(join(t.titulo))}">${escapeHTML(join(t.titulo))}</span><span class="track-artist">${escapeHTML(join(t.artista))}</span>${t.versao?recordingBadges(t.versao):""}</div></div></td><td class="album-cell" title="${escapeHTML(join(t.album))}">${escapeHTML(join(t.album))}</td><td>${t.genero.length ? `<span class="source">${escapeHTML(join(t.genero))}</span>` : `<span class="missing">${tr("Não definido")}</span>`}</td><td><span class="format ${t.formato === "FLAC" ? "flac" : ""}">${t.formato}</span></td><td class="time-cell">${duration(t.duracao_segundos)}</td><td><button class="icon-button" data-info="${t.id}" aria-label="${tr("Ver metadados de")} ${escapeHTML(join(t.titulo))}">${icon("more")}</button></td></tr>`).join("")}</tbody></table></div>`;
}

function breadcrumb(label) { return `<div class="page-topline">${tr("Seu espaço")} ${icon("arrow")} <span>${label}</span></div>`; }
function empty(title, text, name="library", action="") { return `<div class="empty-state">${icon(name)}<h2>${title}</h2><p>${text}</p>${action}</div>`; }
function heading(kicker,title,text,actions="") { return `${breadcrumb(title)}<div class="page-heading"><div><span class="section-kicker">${kicker}</span><h1>${title}</h1><p>${text}</p></div><div class="page-heading-actions">${actions}</div></div>`; }

async function renderHome() {
  const result = await api("/api/library?limit=8");
  state.queue = result.musicas.map(t=>t.id);
  const data = state.summary;
  const sum = data.resumo;
  const albums = featuredAlbums();
  return `${breadcrumb(tr("Visão geral"))}<section class="hero"><div class="hero-copy"><div class="eyebrow">${icon("disc")} ${tr("SUA BIBLIOTECA PESSOAL")}</div><h1>${tr("Sua música.")}<br><span>${tr("Cada detalhe importa.")}</span></h1><p>${tr("Uma coleção com a sua identidade.")}<br>${tr("Descubra o que falta, revise e deixe tudo em harmonia.")}</p><div class="hero-actions"><button class="button primary" data-view="revisao">${tr("Revisar minha coleção")} ${icon("arrow")}</button><button class="button secondary" data-view="biblioteca">${tr("Ver biblioteca")}</button></div></div><div class="hero-art" aria-hidden="true">${albums.map(a=>`<img src="${cover(a.track_id)}" alt="">`).join("")}</div></section><div class="stat-grid">${statCard(tr("Músicas na coleção"),sum.lidos,"music",`${number(sum.formatos?.MP3)} MP3 <span class="green">·</span> ${number(sum.formatos?.FLAC)} FLAC`)}${statCard(tr("Álbuns na biblioteca"),data.total_albuns,"disc",tr("Histórias que merecem ser ouvidas"))}${statCard(tr("Gêneros pendentes"),sum.campos_ausentes?.genero,"tag",tr("Uma coleção pronta para ganhar detalhes"),true)}${statCard(tr("Capas presentes"),sum.com_capa,"image",`<span class="green">${tr("A identidade visual da sua música")}</span>`)}</div><section class="section"><div class="section-heading"><div><h2>${tr("Na sua estante")}</h2><p>${tr("Alguns dos discos que fazem parte de você.")}</p></div><button class="text-button" data-view="albuns">${tr("Ver todos")} ${icon("arrow")}</button></div><div class="album-grid">${albums.map(albumCard).join("")}</div></section><section class="section overview-table"><div class="section-heading"><div><h2>${tr("Faixas da coleção")}</h2><p>${number(sum.lidos)} ${tr("boas razões para apertar o play.")}</p></div><button class="text-button" data-view="biblioteca">${tr("Explorar biblioteca")} ${icon("arrow")}</button></div>${result.musicas.length ? trackTable(result.musicas) : empty(tr("Sua biblioteca está sendo preparada"), tr("A primeira análise pode levar alguns minutos. Você acompanha o progresso no topo da página."))}</section>`;
}

async function renderLibrary() {
  const query = new URLSearchParams({q:state.search, artista:state.searchArtist, album_id:state.searchAlbum, filter:state.filter, versao:state.versionFilter, offset:state.page*50, limit:50});
  const result = await api(`/api/busca?${query}`);
  state.queue = result.musicas.map(t=>t.id);
  const searching = !!(state.search || state.searchArtist || state.searchAlbum);
  const description = searching ? `${number(result.total)} ${tr("faixas para “")}${escapeHTML(state.searchLabel || state.search)}${tr("” · resultados por relevância")}` : `${number(result.total)} ${tr("faixas ·")} ${number(state.summary.total_artistas)} ${tr("créditos de artista")}`;
  return `${heading(tr("SEU SOM, SEU ACERVO"),searching?tr("Resultados da busca"):tr("Todas as suas músicas"),description, `<button class="button ghost" data-action="export-playlist" ${state.summary.job?.estado==="executando"?"disabled":""}>${icon("download")} ${tr("Exportar M3U8")}</button><button class="button ghost" data-action="analyze">${icon("refresh")} ${tr("Atualizar biblioteca")}</button>`)}<div class="table-toolbar"><div class="tabs">${[["todas",tr("Todas as faixas")],["sem-genero",tr("Sem gênero")],["flac","FLAC"],["versoes",tr("Versões e extras")]].map(([key,label])=>`<button class="tab ${state.filter===key?"active":""}" data-filter="${key}">${label}</button>`).join("")}</div><span class="table-note">${icon("shield")} ${tr("Seus arquivos originais")}</span></div>${recordingChips(state.versionFilter,"data-library-version")}${result.musicas.length ? trackTable(result.musicas,state.page*50) : empty(tr("Nenhuma faixa por aqui"), tr("Tente outro termo de busca ou mude os filtros."), "search")}<div class="table-footer"><span>${result.total ? `${number(state.page*50+1)}–${number(Math.min((state.page+1)*50,result.total))} ${tr("de")} ${number(result.total)} ${tr("faixas")}` : tr("0 faixas")}</span><div class="pagination"><button class="button small ghost" data-page="${state.page-1}" ${state.page===0?"disabled":""}>${tr("Anterior")}</button><button class="button small ghost" data-page="${state.page+1}" ${(state.page+1)*50>=result.total?"disabled":""}>${tr("Próxima")}</button></div></div>`;
}

function filteredProposals() {
  const needle = state.search.toLocaleLowerCase("pt-BR");
  return (state.proposals||[]).filter(p=>(state.reviewFilter==="todas" || p.campo===state.reviewFilter) && (!needle || `${p.titulo} ${p.artista} ${p.album} ${join(p.proposto)}`.toLocaleLowerCase("pt-BR").includes(needle)));
}

async function renderReview() {
  if (state.proposals === null) {
    const [proposals, pending] = await Promise.all([api("/api/proposals"),api("/api/unidentified")]);
    state.proposals = proposals.alteracoes;
    state.pending = pending.musicas;
  }
  const reviewTabs = () => `<div class="tabs">${[["todas",tr("Todas")],["genero",tr("Gêneros")],["album",tr("Álbuns")],["pendentes",`${tr("Sem sugestão ·")} ${number(state.pending.length)}`]].map(([key,label])=>`<button class="tab ${state.reviewFilter===key?"active":""}" data-review-filter="${key}">${label}</button>`).join("")}</div>`;
  if (state.reviewFilter === "pendentes") {
    const query = state.search.toLowerCase();
    const rows = state.pending.filter(t=>`${join(t.titulo)} ${join(t.artista)} ${join(t.album)}`.toLowerCase().includes(query));
    return `${heading(tr("UM OLHAR MAIS ATENTO"),tr("Ainda falta conhecer melhor."),`${number(rows.length)} ${tr("músicas sem uma sugestão de gênero segura.")}`)}<div class="review-summary">${icon("search")}<p><strong>${tr("Uma dúvida é melhor que uma tag errada.")}</strong>${tr("Estas faixas foram preservadas. Você pode ativar a consulta ao MusicBrainz e analisar novamente.")}</p><button class="button small ghost" data-action="preferences">${tr("Preferências")}</button></div>${reviewTabs()}${rows.length?trackTable(rows):empty(tr("Nenhuma música pendente neste filtro"),tr("Tente outro termo de busca ou confira as sugestões disponíveis."),"check")}`;
  }
  const rows = filteredProposals();
  const visible = rows.slice(state.page*40,(state.page+1)*40);
  state.visibleProposals = visible;
  const counts = state.summary.resumo.propostas_por_campo || {};
  const titles = {genero:tr("Gênero"), album:tr("Álbum")};
  return `${heading(tr("VOCÊ ESCOLHE O QUE MUDA"),tr("Um pouco mais de harmonia."),`${number(counts.genero)} ${tr("sugestões de gênero e")} ${number(counts.album)} ${tr("de álbum, esperando sua revisão.")}`, `<a class="button ghost" href="/api/export">${icon("download")} ${tr("Exportar prévia")}</a>`)}<div class="review-summary">${icon("shield")}<p><strong>${tr("Sua coleção continua nas suas mãos.")}</strong>${tr("Selecione as alterações que fazem sentido. Cada aplicação inclui um backup completo.")}</p><span class="chip">${tr("Aprovação por alteração")}</span></div><div class="table-toolbar">${reviewTabs()}${rows.length ? `<button class="text-button" data-action="select-filtered">${tr("Selecionar")} ${number(rows.length)} ${tr("sugestões filtradas")}</button>` : ""}</div>${rows.length ? `<div class="table-wrap"><table class="review-table"><thead><tr><th><input class="check" id="check-visible" type="checkbox" aria-label="${tr("Selecionar alterações desta página")}"></th><th>${tr("MÚSICA")}</th><th>${tr("CAMPO")}</th><th>${tr("ATUAL → SUGERIDO")}</th><th>${tr("ORIGEM")}</th></tr></thead><tbody>${visible.map(p=>`<tr><td><input class="check proposal-check" type="checkbox" data-id="${p.id}" aria-label="${tr("Aprovar")} ${titles[p.campo]} ${tr("de")} ${escapeHTML(p.titulo)}" ${state.selected.has(p.id)?"checked":""}></td><td><div class="track-info"><img class="track-cover" src="${cover(p.track_id)}" alt="" loading="lazy"><div class="track-text"><span class="track-name">${escapeHTML(p.titulo)}</span><span class="track-artist">${escapeHTML(p.artista)}</span></div></div></td><td><span class="chip ${p.campo==="album"?"amber":"neutral"}">${titles[p.campo]}</span></td><td><span class="change-old">${escapeHTML(join(p.atual) || tr("Não definido"))}</span><span class="change-new">${icon("arrow")}${escapeHTML(join(p.proposto))}</span>${p.campo==="album"?`<span class="source">${tr("Edição anterior preservada em tag própria")}</span>`:""}</td><td><span class="source ${p.fonte.includes("usuario")?"user":""}" title="${escapeHTML(p.criterio)}">${p.fonte.startsWith("https://musicbrainz.org/")?tr("MusicBrainz · revisão"):p.fonte.includes("usuario")?tr("Sua regra de gênero"):tr("Sugestão de curadoria")}</span></td></tr>`).join("")}</tbody></table></div><div class="table-footer"><span>${number(state.page*40+1)}–${number(Math.min((state.page+1)*40,rows.length))} ${tr("de")} ${number(rows.length)} ${tr("alterações · nada aplicado")}</span><div class="pagination"><button class="button small ghost" data-page="${state.page-1}" ${state.page===0?"disabled":""}>${tr("Anterior")}</button><button class="button small ghost" data-page="${state.page+1}" ${(state.page+1)*40>=rows.length?"disabled":""}>${tr("Próxima")}</button></div></div>` : empty(tr("Tudo revisado por aqui"), tr("Não há sugestões para este filtro. Confira também a lista de músicas sem sugestão."), "check")}<div id="selection-bar"></div>`;
}

function updateSelection() {
  const target = $('#selection-bar');
  if (target) {
    target.className = state.selected.size ? "selection-bar" : "";
    target.innerHTML = state.selected.size ? `<span><strong>${number(state.selected.size)}</strong> ${tr("alterações selecionadas")}</span><div class="tabs"><button class="text-button" data-action="clear-selection">${tr("Limpar seleção")}</button><button class="button primary" data-action="review-apply">${tr("Revisar e aplicar")} ${icon("arrow")}</button></div>` : "";
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
  if (state.albumFilter === "edicoes") {
    const key = a => `${a.artista.toLowerCase()}|${a.album.normalize("NFKC").replace(/\s*[([][^()[\]]*(?:remaster|deluxe|expanded|anniversary)[^()[\]]*[)\]]\s*$/i, "").trim().toLowerCase()}`;
    const groups = new Map();
    for (const a of rows) groups.set(key(a),(groups.get(key(a))||0)+1);
    rows=rows.filter(a=>groups.get(key(a))>1);
  }
  const needle = state.search.toLowerCase();
  if (needle) rows = rows.filter(a=>`${a.album} ${a.artista}`.toLowerCase().includes(needle));
  return `${heading(tr("A SUA ESTANTE, SEM POEIRA"),tr("Discos que contam histórias."),`${number(rows.length)} ${tr("álbuns · edições exibidas como estão nos seus arquivos")}`)}<div class="table-toolbar"><div class="tabs"><button class="tab ${state.albumFilter==="todas"?"active":""}" data-album-filter="todas">${tr("Todos os álbuns")}</button><button class="tab ${state.albumFilter==="edicoes"?"active":""}" data-album-filter="edicoes">${tr("Nomes semelhantes")}</button></div><span class="table-note">${icon("disc")} ${tr("Cada edição tem sua história")}</span></div>${rows.length ? `<div class="album-grid full">${rows.map(albumCard).join("")}</div>` : empty(tr("Nenhum álbum encontrado"), tr("Experimente mudar a busca ou o filtro."), "disc")}`;
}

async function renderHistory() {
  const rows = (await api("/api/history")).execucoes;
  const labels = {concluido:tr("Concluída"),restaurado:tr("Restaurada"),interrompido:tr("Interrompida"),aplicando:tr("Em andamento"),preparando_backups:tr("Preparando backup")};
  return `${heading(tr("CADA MUDANÇA TEM UM CAMINHO DE VOLTA"),tr("Histórico da coleção"), tr("Acompanhe suas alterações e restaure os arquivos de uma execução."))}${rows.length ? rows.map(r=>`<article class="history-row"><span class="history-icon">${icon("history")}</span><div class="history-text"><strong>${number(r.arquivos)} ${r.arquivos===1?tr("arquivo"):tr("arquivos")} · ${r.categoria==="letra"?tr("letra da faixa"):r.tipo==="downloads"?tr("importação de downloads"):tr("atualização de metadados")}</strong><p>${escapeHTML(new Date(r.data).toLocaleString(uiLocale()))} · ${r.tipo==="downloads"?tr("originais com backup · adições podem ser arquivadas ao desfazer"):tr("backup completo")}${r.erro?" · "+escapeHTML(r.erro):""}</p></div><span class="chip ${r.estado==="interrompido"?"amber":""}">${escapeHTML(labels[r.estado]||r.estado)}</span><button class="button small ghost" data-restore="${r.id}" data-restore-kind="${r.categoria==="letra"?"metadados":r.tipo==="downloads"?"downloads":"metadados"}" ${r.estado==="restaurado"?"disabled":""}>${icon("history")} ${r.categoria==="letra"?tr("Restaurar letra"):r.tipo==="downloads"?tr("Desfazer importação"):tr("Restaurar")}</button></article>`).join("") : empty(tr("Sua coleção começa um novo capítulo."), tr("Quando você aplicar as primeiras alterações, os backups e os registros aparecerão aqui. Até lá, seus arquivos seguem como estão."), "history", `<button class="button primary" data-view="revisao">${tr("Conhecer as sugestões")} `+icon("arrow")+'</button>')}`;
}

async function render() {
  const request = ++state.request;
  document.querySelectorAll('.nav-item').forEach(el=>{ el.classList.toggle("active",el.dataset.view===state.view); el.setAttribute("aria-current",el.dataset.view===state.view?"page":"false"); });
  try {
    let html = await ({inicio:renderHome,biblioteca:renderLibrary,downloads:renderSimpleDownloads,"downloads-avancados":renderDownloads,revisao:renderReview,albuns:renderAlbums,historico:renderHistory}[state.view] || renderHome)();
    if (request !== state.request) return;
    if (state.summary.erro) html = `<div class="error-box">${escapeHTML(state.summary.erro)}</div>`+html;
    if (state.summary.erro_protecoes) html = `<div class="error-box"><strong>${tr("Campos protegidos indisponíveis.")}</strong> ${escapeHTML(state.summary.erro_protecoes)} ${tr("As alterações ficam bloqueadas até corrigir esse arquivo.")}</div>`+html;
    $('#content').innerHTML=html;
    setWriteControls();
    applySimpleVersionFilter();
    updateSelection();
  } catch(error) { toast(error.message); }
}

function navigate(view) {
  if (!["inicio","biblioteca","downloads","downloads-avancados","revisao","albuns","historico"].includes(view)) view="inicio";
  state.view=view;
  state.page=0;
  history.replaceState(null,"",`#${view}`);
  window.scrollTo({top:0});
  return render();
}

function modal(content) { $('#modal-content').innerHTML=content; setWriteControls(); if (!$('#modal').open) $('#modal').showModal(); }
function modalHead(title,text) { return `<div class="modal-head"><div><h2>${title}</h2><p>${text}</p></div><button class="icon-button" data-action="close-modal" aria-label="${tr("Fechar")}">${icon("close")}</button></div>`; }
function bytes(value) { return value >= 1073741824 ? `${(value/1073741824).toFixed(2).replace(".",",")} GB` : `${(value/1048576).toFixed(1).replace(".",",")} MB`; }

function showPlaylist(albumId="", previousName) {
  const album = state.albums.find(a=>a.track_id===albumId);
  playlistState.draft = albumId ? {album_id:albumId} : {q:state.search, filter:state.filter, artista:state.searchArtist, album_id:state.searchAlbum, versao:state.versionFilter};
  playlistState.pending = null;
  const name = (previousName ?? (album?.album || state.search || tr("Minha seleção"))).replace(/[\\/:*?"<>|\x00-\x1f\x7f]/g,"_").slice(0,90);
  const scope = album ? `${tr("Álbum:")} ${album.album} · ${album.artista}` : `${tr("Todas as faixas da busca e do filtro atuais, incluindo as outras páginas")}${state.search?` ${tr("· busca:")} ${state.search}`:""}.`;
  modal(`${modalHead(tr("Exportar playlist M3U8"),escapeHTML(scope))}<form id="playlist-form"><div class="modal-body"><label for="playlist-name">${tr("Nome da playlist")}</label><input class="field" id="playlist-name" name="nome" required maxlength="100" value="${escapeHTML(name)}"><div class="backup-note">${icon("folder")}<span>${tr("A playlist ficará na pasta")} <strong>Playlists/</strong> ${tr("da coleção, com caminhos relativos. Para usar em outro disco, copie a coleção junto com essa pasta.")}</span></div></div><div class="modal-actions"><button class="button ghost" type="button" data-action="close-modal">${tr("Cancelar")}</button><button class="button primary" type="submit">${tr("Ver prévia")} ${icon("arrow")}</button></div></form>`);
  $('#playlist-name').focus();
}

document.addEventListener("submit",async event=>{
  if(event.target.id!=="playlist-form")return;
  event.preventDefault();
  const submit=event.target.querySelector('[type="submit"]');
  submit.disabled=true;
  try {
    const data={...playlistState.draft,nome:$('#playlist-name').value};
    const result=await api("/api/playlists/preview",data);
    playlistState.pending={...data,anterior_sha256:result.anterior_sha256};
    playlistState.exists=result.existe;
    modal(`${modalHead(tr("Conferir a playlist"),`${number(result.faixas)} ${tr("faixas em ordem de artista, álbum, disco e faixa.")}`)}<div class="modal-body"><dl class="details-list"><div><dt>${tr("Destino")}</dt><dd>${escapeHTML(result.playlist)}</dd></div><div><dt>${tr("Formato")}</dt><dd>${tr("M3U8 · UTF-8 · caminhos relativos")}</dd></div></dl>${result.existe?`<div class="form-row"><label><input class="check" id="playlist-overwrite" type="checkbox"> ${tr("Substituir a playlist que já existe")}</label><p>${tr("A versão anterior será guardada em backup. Para manter as duas na coleção, volte e escolha outro nome.")}</p></div>`:""}<ul class="confirm-list">${result.amostra.map(t=>`<li><strong>${escapeHTML(join(t.titulo))}</strong><span>${escapeHTML(join(t.artista))}</span></li>`).join("")}</ul>${result.faixas>result.amostra.length?`<p class="download-hint">${tr("E mais")} ${number(result.faixas-result.amostra.length)} ${tr("faixas da seleção.")}</p>`:""}</div><div class="modal-actions"><button class="button ghost" data-action="edit-playlist">${tr("Alterar nome")}</button><button class="button primary" data-action="confirm-playlist">${icon("download")} ${tr("Salvar playlist")}</button></div>`);
  }catch(error){submit.disabled=false;toast(error.message);}
});

function confirmApply() {
  const selected = state.proposals.filter(p=>state.selected.has(p.id));
  if (!selected.length) return;
  const files = new Map(selected.map(p=>[p.arquivo,p.tamanho_bytes]));
  const size = [...files.values()].reduce((a,b)=>a+b,0);
  modal(`${modalHead(tr("Vamos cuidar desses detalhes?"),`${number(selected.length)} ${tr("alterações em")} ${number(files.size)} ${tr("arquivos. Confira a seleção antes de continuar.")}`)}<div class="modal-body"><div class="backup-note">${icon("shield")}<span>${tr("Será criado um backup completo de")} <strong>${bytes(size)}</strong> ${tr("em")} <strong>backups/</strong>${tr(". Depois, você poderá restaurar esta execução pelo histórico.")}</span></div><ul class="confirm-list">${selected.map(p=>`<li><strong>${escapeHTML(p.artista)} — ${escapeHTML(p.titulo)}</strong><span>${p.campo==="genero"?tr("Gênero"):tr("Álbum")}: ${escapeHTML(join(p.atual)||tr("Não definido"))} → ${escapeHTML(join(p.proposto))}</span>${p.campo==="album"?`<span>${tr("O nome anterior do álbum será preservado em uma tag própria.")}</span>`:""}</li>`).join("")}</ul></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Voltar à revisão")}</button><button class="button primary" data-action="confirm-apply">${icon("check")} ${tr("Criar backup e aplicar")}</button></div>`);
}

async function showDetails(id) {
  const track = await api(`/api/track?id=${encodeURIComponent(id)}`);
  const pairs = [["titulo",tr("Título"),join(track.titulo)],["artista",tr("Artista"),join(track.artista)],["artista_album",tr("Artista do álbum"),join(track.artista_album)],["album",tr("Álbum"),join(track.album)],["genero",tr("Gênero"),join(track.genero)],["ano",tr("Ano"),join(track.ano)],["faixa",tr("Faixa"),join(track.faixa)],["disco",tr("Disco"),join(track.disco)],[null,tr("Áudio"),`${track.formato} · ${duration(track.duracao_segundos)} · ${Math.round((track.bitrate_bps||0)/1000)} kbps`],[null,tr("Capas"),`${track.capas.length} ${tr("imagens preservadas")}`],[null,tr("Arquivo"),track.arquivo]];
  modal(`${modalHead(tr("Os detalhes da faixa"), tr("Metadados lidos do seu arquivo. Proteja campos para preservar suas escolhas."))}<div class="detail-hero"><img src="${cover(id)}" alt="${tr("Capa do álbum")}"><div><h3>${escapeHTML(join(track.titulo))}</h3><p>${escapeHTML(join(track.artista))}</p><button class="button small primary" data-play="${id}">${icon("play")} ${tr("Ouvir")}</button><button class="button small ghost" data-lyrics="${escapeHTML(id)}">${icon("lyrics")} ${tr("Letra")}</button></div></div><div class="modal-body">${track.erro_protecoes?`<div class="error-box">${escapeHTML(track.erro_protecoes)}</div>`:""}<dl class="details-list">${pairs.map(([field,k,v])=>`<div><dt>${k}</dt><dd>${escapeHTML(v||tr("Não definido"))}${field?protectionControl(track,field):""}</dd></div>`).join("")}</dl></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Fechar")}</button></div>`);
}

function showSettings() {
  const options=settings();
  modal(`${modalHead(tr("Do seu jeito."),tr("Preferências para a próxima análise da coleção."))}<div class="modal-body"><div class="form-row"><label><input type="checkbox" class="check" id="use-musicbrainz" ${options.musicbrainz?"checked":""}> ${tr("Consultar MusicBrainz para gêneros sem regra local")}</label><p>${tr("Consulta nomes de artista e álbum. As sugestões sempre passam pela sua aprovação. Nenhum arquivo de áudio é enviado.")}</p><input id="musicbrainz-contact" class="field" type="text" placeholder="${tr("Seu e-mail ou URL de contato")}" aria-label="${tr("Contato do cliente MusicBrainz")}" value="${escapeHTML(options.contato)}"></div><div class="form-row"><label>${icon("folder")} ${tr("Biblioteca atual")}</label><p>${escapeHTML(state.summary.biblioteca)}</p></div><div class="form-row"><label>${icon("tag")} ${tr("Regras de gênero editáveis")}</label><p>${tr("As propostas locais ficam em genre_rules.csv. Gêneros que já existem nos arquivos são preservados.")}</p></div><div class="form-row"><label>${icon("lock")} ${tr("Escolhas preservadas")}</label><p><button class="text-button" data-action="protection-list">${tr("Ver campos protegidos e desbloquear")}</button></p></div><div class="form-row"><label>${icon("download")} ${tr("Mais opções")}</label><p><button class="text-button" data-view="downloads-avancados">${tr("Downloads avançados com revisão e importação")}</button></p></div></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Cancelar")}</button><button class="button primary" data-action="save-settings">${tr("Salvar preferências")}</button></div>`);
}

async function analyze() {
  const options=settings();
  if (options.musicbrainz && !options.contato.trim()) { showSettings(); toast(tr("Informe seu contato para identificar o cliente MusicBrainz.")); return; }
  await api("/api/analyze",options);
  await updateState();
  toast(tr("Análise iniciada. Acompanhe o progresso no topo."));
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
  $('#player-title').dataset.i18nUser="true";$('#player-title').textContent=join(track.titulo);
  $('#player-artist').dataset.i18nUser="true";$('#player-artist').textContent=join(track.artista);
  $('#player-cover').innerHTML=`<img src="${cover(id)}" alt="">`;
  $('#audio-quality').textContent=`${track.formato} · ${Math.round((track.bitrate_bps||0)/1000)} KBPS`;
  audio.src=`/api/audio?id=${encodeURIComponent(id)}`;
  $('#play-toggle').disabled=false;
  $('#seek').disabled=false;
  $('#time-total').textContent=duration(track.duracao_segundos);
  const inQueue=state.queue.includes(id);
  $('#previous').disabled=!inQueue || state.queue.indexOf(id)===0;
  $('#next').disabled=!inQueue || state.queue.indexOf(id)===state.queue.length-1;
  await audio.play();
}
audio.addEventListener("play",()=>{ $('#play-toggle').innerHTML=icon("pause"); $('#play-toggle').setAttribute("aria-label",tr("Pausar")); $('#playing-dot').hidden=false; });
audio.addEventListener("pause",()=>{ $('#play-toggle').innerHTML=icon("play"); $('#play-toggle').setAttribute("aria-label",tr("Reproduzir")); $('#playing-dot').hidden=true; });
audio.addEventListener("timeupdate",()=>{ $('#time-current').textContent=duration(audio.currentTime); $('#seek').value=Number.isFinite(audio.duration)&&audio.duration>0 ? audio.currentTime/audio.duration*1000 : 0; });
audio.addEventListener("loadedmetadata",()=>{ $('#time-total').textContent=duration(audio.duration); });
audio.addEventListener("error",()=>toast(tr("Não foi possível reproduzir este arquivo no navegador.")));
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
    else if(button.dataset.album){ state.search=button.dataset.album;state.searchArtist="";state.searchAlbum="";state.searchLabel="";$('#search').value=state.search;state.filter="todas";navigate("biblioteca"); }
    else if(button.dataset.libraryVersion){state.versionFilter=button.dataset.libraryVersion;state.page=0;await render();}
    else if(button.dataset.filter){state.filter=button.dataset.filter;state.page=0;await render();}
    else if(button.dataset.reviewFilter){state.reviewFilter=button.dataset.reviewFilter;state.page=0;await render();}
    else if(button.dataset.albumFilter){state.albumFilter=button.dataset.albumFilter;await render();}
    else if(button.dataset.page!==undefined){state.page=Math.max(0,Number(button.dataset.page));await render();}
    else if(button.dataset.restore){
      const imported=button.dataset.restoreKind==="downloads";
      modal(`${modalHead(imported?tr("Desfazer esta importação?"):tr("Voltar ao estado anterior?"),imported?tr("Os arquivos adicionados serão arquivados fora da coleção. Substituições e alterações de tags voltarão aos backups."):tr("Os arquivos desta execução voltarão exatamente ao estado do backup."))}<div class="modal-body"><div class="backup-note">${icon("history")}<span>${imported?tr("Edições posteriores em arquivos já importados impedem a restauração. Entradas que não chegaram a ser importadas serão preservadas e podem gerar avisos."):tr("Se algum arquivo recebeu outras edições depois desta execução, a restauração será recusada para preservar essas mudanças.")}</span></div></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Cancelar")}</button><button class="button primary" data-action="confirm-restore" data-id="${button.dataset.restore}">${imported?tr("Arquivar adições e restaurar"):tr("Restaurar arquivos")}</button></div>`);
    } else {
      const action=button.dataset.action;
      if(action==="close-modal")$('#modal').close();
      else if(action==="export-playlist")showPlaylist();
      else if(action==="edit-playlist")showPlaylist(playlistState.draft?.album_id||"",playlistState.pending?.nome);
      else if(action==="confirm-playlist"){
        if(!playlistState.pending)return;
        const overwrite=!!$('#playlist-overwrite')?.checked;
        if(playlistState.exists&&!overwrite)throw new Error(tr("Escolha substituir a playlist existente ou volte e altere o nome."));
        button.disabled=true;
        const result=await api("/api/playlists/export",{...playlistState.pending,sobrescrever:overwrite});
        playlistState.pending=null;
        modal(`${modalHead(tr("Playlist salva"),`${number(result.encontradas)} ${tr("de")} ${number(result.faixas)} ${tr("caminhos conferidos.")}`)}<div class="modal-body"><dl class="details-list"><div><dt>${tr("Arquivo")}</dt><dd>${escapeHTML(result.playlist)}</dd></div><div><dt>${tr("Para transportar")}</dt><dd>${tr("Copie a coleção junto com a pasta Playlists/.")}</dd></div>${result.backup?`<div><dt>${tr("Cópia anterior")}</dt><dd>${escapeHTML(result.backup)}</dd></div><div><dt>${tr("Para restaurar a playlist")}</dt><dd>${tr("Copie o backup para o caminho da playlist acima.")}</dd></div>`:""}</dl>${result.ausentes.length?`<p class="download-warning">${number(result.ausentes.length)} ${tr("arquivos ficaram indisponíveis durante a gravação. Confira a coleção.")}</p>`:""}</div><div class="modal-actions"><button class="button primary" data-action="close-modal">${tr("Concluir")}</button></div>`);
      }
      else if(action==="preferences")showSettings();
      else if(action==="select-filtered"){filteredProposals().forEach(p=>state.selected.add(p.id));updateSelection();}
      else if(action==="clear-selection"){state.selected.clear();updateSelection();}
      else if(action==="review-apply")confirmApply();
      else if(action==="confirm-apply"){
        button.disabled=true;
        await api("/api/apply",{ids:[...state.selected]});$('#modal').close();await updateState();toast(tr("Execução iniciada. O backup vem primeiro."));
      } else if(action==="confirm-restore"){
        button.disabled=true;
        await api("/api/restore",{id:button.dataset.id});$('#modal').close();await updateState();
      } else if(action==="save-settings"){
        const options={musicbrainz:$('#use-musicbrainz').checked,contato:$('#musicbrainz-contact').value.trim()};
        if(options.musicbrainz&&!options.contato){toast(tr("Informe um e-mail ou URL real de contato."));$('#musicbrainz-contact').focus();return;}
        localStorage.setItem("acervo-options",JSON.stringify(options));$('#modal').close();toast(tr("Preferências salvas para a próxima análise."));
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
    $('#play-toggle').setAttribute("aria-label",tr(audio.paused?"Reproduzir":"Pausar"));
    if(state.current?.id?.startsWith("stage-"))$('#player-artist').textContent=join(state.current.artista)+" "+tr("· prévia do download");
    const scope=typeof searchState!=="undefined"?searchState.scope:"biblioteca";
    const label=tr(scope==="global"?"Buscar músicas ou artistas no catálogo global":"Buscar músicas, artistas ou álbuns");
    $('#search').placeholder=label;$('#search').setAttribute("aria-label",label);
    if(typeof refreshLyricsLanguage==="function")refreshLyricsLanguage();
    await render();
  }catch(error){toast(error.message);}
});

async function poll() {
  try {
    const previous=state.summary?.analisado_em;
    const previousLibrary=state.summary?.biblioteca_revisao;
    const previousPolicy=state.summary?.protecoes_revisao;
    const previousPolicyError=state.summary?.erro_protecoes;
    await updateState();
    const job=state.summary.job;
    const jobKey=job?`${job.id}:${job.estado}`:"";
    if(job?.estado==="erro"&&state.lastJob!==jobKey)toast(job.mensagem);
    if(typeof captureLyricsJob==="function")await captureLyricsJob(job);
    const simpleChanged=captureSimpleDownloadJob(job);
    if(state.view==="downloads"&&simpleChanged)await render();
    if(job?.tipo==="download_direto"&&state.lastJob!==jobKey&&job.estado==="concluido"&&job.resultado?.avisos?.length)toast(job.resultado.avisos.join(" "));
    if(state.view==="downloads-avancados"&&state.lastJob!==jobKey&&job?.estado!=="executando"){
      if(job?.tipo==="catalogo"&&job.resultado?.lote)await openDownloadBatch(job.resultado.lote);
      else await render();
    }
    if(previous!==state.summary.analisado_em || previousLibrary!==state.summary.biblioteca_revisao || previousPolicy!==state.summary.protecoes_revisao || previousPolicyError!==state.summary.erro_protecoes){state.proposals=null;state.selected.clear();state.albums=(await api("/api/albums")).albuns;await render();}
    state.lastJob=jobKey;
  } catch(error){if(!$('#toast').hidden)return;toast(tr("A interface perdeu a conexão. Verifique se o programa continua aberto."));}
  finally {setTimeout(poll,2000);}
}

(async()=>{
  try {
    await updateState();
    state.albums=(await api("/api/albums")).albuns;
    navigate(location.hash.slice(1)||"inicio");
    poll();
  }catch(error){$('#content').innerHTML=empty(tr("Não conseguimos abrir a biblioteca"),escapeHTML(error.message),"folder");}
})();
