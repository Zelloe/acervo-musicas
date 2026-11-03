"use strict";

const QUEUE_PREVIEW = 100;
const playback = {base:[], order:[], index:-1, tracks:new Map(), shuffle:false, repeat:"off", serial:0, tab:"queue"};

try {
  const saved = JSON.parse(localStorage.getItem("acervo-playback") || "{}");
  playback.shuffle = saved.shuffle === true;
  if (["off","all","one"].includes(saved.repeat)) playback.repeat = saved.repeat;
} catch {}

function savePlaybackModes() {
  try { localStorage.setItem("acervo-playback", JSON.stringify({shuffle:playback.shuffle, repeat:playback.repeat})); } catch {}
}

function shuffledIds(ids) {
  const result = [...ids];
  for (let i = result.length-1; i > 0; i--) {
    const j = Math.floor(Math.random()*(i+1));
    [result[i],result[j]] = [result[j],result[i]];
  }
  return result;
}

function arrangeQueue(currentId) {
  const known = playback.base.includes(currentId);
  if (playback.shuffle) playback.order = [...(known ? [currentId] : []), ...shuffledIds(playback.base.filter(id=>id!==currentId))];
  else playback.order = [...playback.base];
  playback.index = known ? playback.order.indexOf(currentId) : -1;
}

function setQueue(tracks, currentId) {
  playback.tracks = new Map(tracks.map(track=>[track.id,track]));
  playback.base = tracks.map(track=>track.id);
  arrangeQueue(currentId);
}

function clearPlayback() {
  setQueue([],null);
  updatePlayerControls();
  renderNowView();
}

function neighbour(delta) {
  const {order,index,repeat} = playback;
  if (!order.length || index < 0) return null;
  const target = index+delta;
  if (target >= 0 && target < order.length) return order[target];
  return repeat === "all" ? order[(target+order.length)%order.length] : null;
}

async function startTrack(id) {
  const serial = ++playback.serial;
  if (state.current?.id === id) {
    audio.currentTime = 0;
    await audio.play();
    return;
  }
  await play(id,serial);
}

async function queueFor(button) {
  const id = button?.dataset.play;
  if (button?.closest(".album-card")) {
    return (await api(`/api/queue?${new URLSearchParams({album_id:id, sort:"album"})}`)).tracks;
  }
  if (state.view === "library") return (await api(`/api/queue?${new URLSearchParams(libraryParams())}`)).tracks;
  if (state.queue.includes(id)) return Promise.all(state.queue.map(item=>api(`/api/track?id=${encodeURIComponent(item)}`)));
  return null;
}

// A list click plays that list, including rows the infinite scroll has not loaded.
async function startPlayback(button) {
  const id = button.dataset.play, serial = ++playback.serial;
  if (state.current?.id === id) return play(id,serial);
  const tracks = await queueFor(button);
  if (serial !== playback.serial) return;
  if (tracks?.some(track=>track.id===id)) setQueue(tracks,id);
  else setQueue([],null);
  await play(id,serial);
}

async function shuffleAll() {
  const serial = ++playback.serial;
  const {tracks} = await api(`/api/queue?${new URLSearchParams(libraryParams())}`);
  if (serial !== playback.serial || !tracks.length) return;
  playback.shuffle = true;
  savePlaybackModes();
  setQueue(tracks,null);
  playback.index = 0;
  updatePlayerControls();
  await startTrack(playback.order[0]);
  openNowView();
}

function syncPlayback(track) {
  playback.index = playback.order.indexOf(track.id);
  if (playback.index < 0) setQueue([track],track.id);
  if (!playback.tracks.has(track.id)) playback.tracks.set(track.id,track);
  $('#now-toggle').disabled = false;
  updatePlayerControls();
  renderNowView();
}

function updatePlayerControls() {
  const shuffleLabel = tr(playback.shuffle ? "Shuffle: on" : "Shuffle: off");
  const repeatLabel = tr(`Repeat: ${playback.repeat}`);
  document.querySelectorAll('[data-action="toggle-shuffle"]').forEach(button=>{
    button.classList.toggle("mode-on",playback.shuffle);
    button.setAttribute("aria-pressed",String(playback.shuffle));
    button.setAttribute("aria-label",shuffleLabel);
    button.title = shuffleLabel;
    const label = button.querySelector(".mode-label");
    if (label) label.textContent = shuffleLabel;
  });
  document.querySelectorAll('[data-action="cycle-repeat"]').forEach(button=>{
    button.classList.toggle("mode-on",playback.repeat !== "off");
    button.setAttribute("aria-label",repeatLabel);
    button.title = repeatLabel;
    button.querySelector(".repeat-icon use").setAttribute("href",playback.repeat === "one" ? "#i-repeat-one" : "#i-repeat");
    const label = button.querySelector(".mode-label");
    if (label) label.textContent = repeatLabel;
  });
  $('#previous').disabled = !neighbour(-1);
  $('#next').disabled = !neighbour(1);
}

function upcomingTracks() {
  const {order,index,repeat} = playback;
  if (index < 0) return [];
  return [...order.slice(index+1), ...(repeat === "all" ? order.slice(0,index) : [])];
}

function renderQueue() {
  const upcoming = upcomingTracks(), visible = upcoming.slice(0,QUEUE_PREVIEW);
  const rows = visible.map(id=>{
    const track = playback.tracks.get(id);
    if (!track) return "";
    return `<li><button class="queue-row" data-queue-play="${escapeHTML(id)}"><img src="${cover(id)}" alt="" loading="lazy"><span class="queue-text"><strong>${escapeHTML(join(track.title))}</strong><span>${escapeHTML(join(track.artist))}</span></span><span class="queue-time">${duration(track.duration_seconds)}</span></button></li>`;
  }).join("");
  const more = upcoming.length-visible.length;
  $('#now-queue').innerHTML = `<h3>${tr("Up next")} · ${number(upcoming.length)}</h3>${rows ? `<ol>${rows}</ol>` : `<p class="queue-empty">${tr("Nothing else in the queue")}</p>`}${more > 0 ? `<p class="queue-empty">${tr("and {count} more").replace("{count}",number(more))}</p>` : ""}`;
}

function renderNowView() {
  const track = state.current;
  if ($('#now-view').hidden || !track) return;
  const real = playback.index >= 0 && !track.id.startsWith("stage-");
  $('#now-title').dataset.i18nUser = "true";
  $('#now-title').textContent = join(track.title);
  $('#now-artist').dataset.i18nUser = "true";
  $('#now-artist').textContent = join(track.artist);
  $('#now-album').dataset.i18nUser = "true";
  $('#now-album').textContent = [join(track.album), join(track.year||[]), track.format].filter(Boolean).join(" · ");
  $('#now-cover').innerHTML = real ? `<img src="${cover(track.id)}" alt="">` : icon("disc");
  const backdrop = $('#now-backdrop');
  backdrop.hidden = !real;
  if (real && backdrop.getAttribute("src") !== cover(track.id)) backdrop.src = cover(track.id);
  renderQueue();
}

function setNowTab(tab) {
  playback.tab = tab;
  document.querySelectorAll('[data-now-tab]').forEach(button=>{
    const active = button.dataset.nowTab === tab;
    button.classList.toggle("active",active);
    button.setAttribute("aria-pressed",String(active));
  });
  $('#now-queue').hidden = tab !== "queue";
  $('#now-lyrics').hidden = tab !== "lyrics";
  if (tab === "lyrics") {
    $('#now-lyrics').append($('#lyrics-panel'));
    if ($('#lyrics-panel').hidden) showLyrics(state.current?.id);
  } else if (!$('#lyrics-panel').hidden) closeLyrics();
}

function openNowView() {
  if (!state.current) return;
  $('#now-view').hidden = false;
  document.body.classList.add("now-open");
  $('#now-toggle').setAttribute("aria-expanded","true");
  renderNowView();
  setNowTab(playback.tab === "lyrics" && !state.current.id.startsWith("stage-") ? "lyrics" : "queue");
  $('#now-close').focus();
}

function closeNowView() {
  if (!$('#lyrics-panel').hidden) closeLyrics();
  $('#now-view').after($('#lyrics-panel'));
  $('#now-view').hidden = true;
  document.body.classList.remove("now-open");
  $('#now-toggle').setAttribute("aria-expanded","false");
  if (document.fullscreenElement) document.exitFullscreen().catch(()=>{});
  $('#now-toggle').focus();
}

function updateFullscreenButton() {
  const label = tr(document.fullscreenElement ? "Exit full screen" : "Full screen");
  $('#now-fullscreen').setAttribute("aria-label",label);
  $('#now-fullscreen').title = label;
  $('#now-fullscreen').classList.toggle("mode-on",!!document.fullscreenElement);
}

function refreshPlayerLanguage() {
  updatePlayerControls();
  updateFullscreenButton();
  renderNowView();
}

audio.addEventListener("ended",()=>{
  if (playback.repeat === "one") { startTrack(state.current.id).catch(error=>toast(error.message)); return; }
  const next = neighbour(1);
  if (next) startTrack(next).catch(error=>toast(error.message));
});
for (const [selector,delta] of [["#previous",-1],["#next",1]]) $(selector).addEventListener("click",()=>{
  const id = neighbour(delta);
  if (id) startTrack(id).catch(error=>toast(error.message));
});
$('#now-toggle').addEventListener("click",()=>{ if ($('#now-view').hidden) openNowView(); else closeNowView(); });
$('#player-cover').addEventListener("click",()=>{ if (state.current && $('#now-view').hidden) openNowView(); });
$('#now-close').addEventListener("click",closeNowView);
$('#now-fullscreen').hidden = !document.documentElement.requestFullscreen;
$('#now-fullscreen').addEventListener("click",()=>{
  (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen()).catch(error=>toast(error.message));
});
document.addEventListener("fullscreenchange",updateFullscreenButton);

// Inside the now playing view the lyrics live in a tab, so these act before lyrics.js.
document.addEventListener("click",event=>{
  if (!$('#now-view').hidden && event.target.closest('#lyrics-toggle')) {
    event.stopPropagation();
    setNowTab("lyrics");
  }
},true);
document.addEventListener("keydown",event=>{
  if (event.key === "Escape" && !$('#now-view').hidden && !$('#modal').open) {
    event.preventDefault();
    event.stopPropagation();
    closeNowView();
  }
},true);

document.addEventListener("click",event=>{
  const button = event.target.closest("button");
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  if (action === "toggle-shuffle") {
    playback.shuffle = !playback.shuffle;
    savePlaybackModes();
    arrangeQueue(state.current?.id);
    updatePlayerControls();
    renderNowView();
  } else if (action === "cycle-repeat") {
    playback.repeat = {off:"all",all:"one",one:"off"}[playback.repeat];
    savePlaybackModes();
    updatePlayerControls();
    renderNowView();
  } else if (action === "shuffle-all") {
    shuffleAll().catch(error=>toast(error.message));
  } else if (action === "sort-direction") {
    state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
    state.page = 0;
    render();
  } else if (button.dataset.queuePlay) {
    startTrack(button.dataset.queuePlay).catch(error=>toast(error.message));
  } else if (button.dataset.nowTab) {
    setNowTab(button.dataset.nowTab);
  }
});
function toggleSortMenu(open, focusTrigger = false) {
  const menu = $('#library-sort-options'), trigger = $('#library-sort');
  if (!menu) return;
  menu.hidden = !open;
  trigger.setAttribute("aria-expanded",String(open));
  if (open) (menu.querySelector('[aria-selected="true"]') || menu.firstElementChild).focus();
  else if (focusTrigger) trigger.focus();
}

async function chooseSort(sort) {
  if (sort !== state.sort) {
    state.sort = sort;
    state.sortDirection = NEWEST_FIRST.has(sort) ? "desc" : "asc";
    state.page = 0;
    await render();
  } else toggleSortMenu(false);
  $('#library-sort')?.focus();
}

document.addEventListener("click",event=>{
  const trigger = event.target.closest('#library-sort'), option = event.target.closest('[data-sort]');
  if (trigger) toggleSortMenu($('#library-sort-options').hidden);
  else if (option) chooseSort(option.dataset.sort).catch(error=>toast(error.message));
  else toggleSortMenu(false);
});
document.addEventListener("keydown",event=>{
  if (event.target.id === "library-sort" && ["ArrowDown","ArrowUp"].includes(event.key)) {
    event.preventDefault();
    toggleSortMenu(true);
    return;
  }
  const menu = $('#library-sort-options');
  if (!menu || menu.hidden) return;
  const options = [...menu.children], index = options.indexOf(document.activeElement);
  const target = {ArrowDown:options[(index+1)%options.length],ArrowUp:options[(index-1+options.length)%options.length],Home:options[0],End:options[options.length-1]}[event.key];
  if (target) {
    event.preventDefault();
    target.focus();
  } else if (event.key === "Escape" || event.key === "Tab") {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); }
    toggleSortMenu(false,event.key === "Escape");
  }
},true);
updatePlayerControls();
