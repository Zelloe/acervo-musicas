"use strict";

const downloadState = {batchId:null, batch:null, fetchIds:new Set(), choices:new Map(), draft:{}, pending:null};
const fieldNames = {title:"Title",artist:"Artist",album_artist:"Album artist",album:"Album",genre:"Genre",year:"Year",track:"Track",disc:"Disc"};
const batchLabels = {querying:"Looking up",ready:"Catalog ready",downloading:"Downloading",review:"Ready for review",cancelled:"Canceled",error:"Needs attention",imported:"Imported"};
const stagedURL = (kind,batch,row) => `/api/downloads/${kind}?${new URLSearchParams({id:batch,track:row})}`;

function downloadForm(info) {
  const draft=downloadState.draft;
  const input=(name,label,placeholder="")=>`<label>${label}<input class="field" name="${name}" value="${escapeHTML(draft[name]||"")}" placeholder="${escapeHTML(placeholder)}"></label>`;
  const select=(name,label,items)=>`<label>${label}<select class="field" name="${name}">${items.map(([v,t])=>`<option value="${escapeHTML(v)}" ${draft[name]===v?"selected":""}>${escapeHTML(t)}</option>`).join("")}</select></label>`;
  const busy=state.summary.job?.status==="running";
  return `<form id="download-form" class="download-panel"><div class="section-heading"><div><h2>${tr("More music in your collection.")}</h2><p>${tr("Start with the catalog. You decide what joins your collection.")}</p></div><span class="chip">${tr("1 · Prepare")}</span></div><div class="download-form-grid"><label class="wide">${tr("Link or search")}<input class="field" name="query" required maxlength="1200" placeholder="${tr("Spotify or YouTube link, or artist and album")}" value="${escapeHTML(draft.query||"")}"></label>${select("tool",tr("Tool"),[["spotdl",tr("spotDL · Spotify catalog and YouTube audio")],["yt-dlp",tr("yt-dlp · direct YouTube link")]])}${select("mode",tr("What would you like to do?"),[["missing",tr("Download missing tracks only")],["full",tr("Prepare a full album again")],["metadata",tr("Unify existing metadata only")]])}${input("album",tr("Unified album name"),tr("Keep the reference name"))}${input("genre",tr("Genre"),tr("Suggest from source and local rules"))}${input("artist",tr("Artist"),tr("Use source credits"))}${input("album_artist",tr("Album artist"),tr("Use the main artist"))}${input("year",tr("Year / date"),tr("YYYY or YYYY-MM-DD"))}${select("bitrate",tr("MP3 · output bitrate"),[["auto",tr("Automatic")],["320k","320 kbps"],["256k","256 kbps"],["192k","192 kbps"],["128k","128 kbps"]])}${select("destination",tr("Existing folder for new tracks"),info.folders.map(p=>[p,p.replace(/^\/home\/[^/]+/,"~")]))}</div><div class="download-options"><label><input class="check" type="checkbox" name="unify_album" ${draft.unify_album!==false?"checked":""}> ${tr("Suggest the base name for remastered / deluxe editions")}</label><label><input class="check" type="checkbox" name="force_genre" ${draft.force_genre?"checked":""}> ${tr("Also suggest replacing existing genres")}</label></div><p class="download-hint">${tr("spotDL uses Spotify as a catalog and finds audio on YouTube. The output bitrate does not improve source quality. New files will be MP3; an existing FLAC can receive tags but will not be replaced with MP3.")}</p><div class="download-tools">${Object.entries(info.tools).filter(([k])=>k!=="deno").map(([k,v])=>`<span class="chip ${v.available?"":"amber"}">${escapeHTML(k)} · ${v.available?tr("available"):tr("not installed")}</span>`).join("")}</div><div class="download-actions"><button class="button primary" type="submit" ${busy?"disabled":""}>${tr("Look up and compare")} ${icon("arrow")}</button></div></form>`;
}

function selectedLocal(row,choice) {
  return row.matches.find(m=>m.id===choice?.target) || (row.matches.length===1?row.matches[0]:null);
}

function diffHTML(changes) {
  return Object.entries(changes||{}).map(([field,c])=>`<div class="download-diff"><span>${escapeHTML(tr(fieldNames[field]||field))}</span><span class="change-old">${escapeHTML(join(c.current)||tr("Not set"))}</span><span class="change-new">${icon("arrow")}${escapeHTML(join(c.proposed))}</span></div>`).join("");
}

function downloadRow(batch,row,index) {
  const choice=downloadState.choices.get(row.id)||{};
  const local=selectedLocal(row,choice);
  const available=row.status==="downloaded";
  const busy=state.summary.job?.status==="running";
  const imported=!!batch.import;
  const canFetch=!imported && batch.mode!=="metadata" && !available && (batch.mode==="full"||!row.matches.length);
  const metadata=row.metadata;
  const art=available&&row.real.covers.length?`<img src="${stagedURL("cover",batch.id,row.id)}" alt="">`:`<div class="download-art">${icon("disc")}</div>`;
  const options=[['',tr("Keep the library as it is")]];
  if(row.matches.some(m=>Object.keys(m.changes).length))options.push(["metadata",tr("Apply only the suggested tags")]);
  if(available)options.push(["add",row.matches.length?tr("Add as another version"):tr("Add to library")]);
  if(available && row.matches.some(m=>m.format==="MP3"))options.push(["replace",tr("Replace an MP3 · with backup")]);
  const changes=choice.action==="metadata"?local?.changes:choice.action==="replace"?local?.replacement_changes:null;
  return `<article class="download-track" data-row="${row.id}"><div class="download-track-head"><div class="download-cover">${art}</div><div class="download-title"><span class="section-kicker">${tr("TRACK")} ${escapeHTML(join(metadata.track)||index+1)} ${tr("· DISC")} ${escapeHTML(join(metadata.disc))}</span><h3>${escapeHTML(join(metadata.title))}</h3><p>${escapeHTML(join(metadata.artist))} · ${escapeHTML(join(metadata.album)||tr("Album not specified"))}</p></div><span class="chip ${row.status==="error"?"amber":""}">${available?tr("Audio ready"):row.matches.length?`${row.matches.length} ${tr("local version")}`:row.status==="error"?tr("Failed"):tr("New track")}</span></div><div class="download-tags"><span>${icon("tag")}${escapeHTML(join(metadata.genre)||tr("Genre missing"))}</span><span>${escapeHTML(join(metadata.year)||tr("Year missing"))}</span><span>${duration(row.source.duration)}</span></div><p class="source">${tr("Genre:")} ${escapeHTML(row.genre_source)}</p>${row.matches.map(m=>`<div class="local-match"><span class="format ${m.format==="FLAC"?"flac":""}">${m.format}</span><span>${escapeHTML(m.file.split("/").pop())}</span><span>${duration(m.duration_seconds)}</span></div>`).join("")}${[...new Set(row.warnings)].map(v=>`<p class="download-warning">${escapeHTML(v)}</p>`).join("")}${row.error?`<p class="download-warning">${escapeHTML(row.error)}</p>`:""}<div class="download-row-actions">${canFetch?`<label><input class="check download-check" type="checkbox" data-id="${row.id}" ${downloadState.fetchIds.has(row.id)?"checked":""} ${busy?"disabled":""}> ${tr("Download for review")}</label>`:""}${available?`<button class="button small ghost" data-stage-play="${row.id}">${icon("play")} ${tr("Listen to download")}</button>`:""}${row.status==="error"?`<button class="text-button" data-download-log="${row.id}">${tr("View log")}</button>`:""}</div>${!imported?`<label class="import-choice">${tr("When approved")}<select class="field import-action" data-id="${row.id}" ${busy?"disabled":""}>${options.map(([value,label])=>`<option value="${value}" ${choice.action===value?"selected":""}>${label}</option>`).join("")}</select></label>${choice.action&&choice.action!=="add"&&row.matches.length>1?`<label class="import-choice">${tr("Local file to change")}<select class="field import-target" data-id="${row.id}"><option value="">${tr("Choose a version…")}</option>${row.matches.map(m=>`<option value="${m.id}" ${choice.target===m.id?"selected":""}>${escapeHTML(m.format+" · "+m.file.split("/").pop())}</option>`).join("")}</select></label>`:""}${changes?`<div class="download-diffs">${diffHTML(changes)}</div>`:""}${choice.action==="add"?`<p class="source">${tr("New file:")} ${escapeHTML(row.new_file.split("/").pop())}</p>`:""}`:""}</article>`;
}

async function openDownloadBatch(id) {
  downloadState.batchId=id;
  downloadState.batch=await api(`/api/downloads/batch?id=${encodeURIComponent(id)}`);
  downloadState.choices.clear();
  downloadState.fetchIds=new Set(downloadState.batch.tracks.filter(r=>!r.matches.length&&r.status!=="downloaded"&&downloadState.batch.mode!=="metadata").map(r=>r.id));
  await render();
}

async function renderDownloads() {
  const info=await api("/api/downloads");
  const list=`<section class="download-panel"><div class="section-heading"><div><h2>${tr("Your review batches")}</h2><p>${tr("Downloads stay outside your collection until approved.")}</p></div></div>${info.batches.length?`<div class="batch-list">${info.batches.map(b=>`<button class="batch-item ${downloadState.batchId===b.id?"active":""}" data-batch="${b.id}"><span>${icon("disc")}</span><span><strong>${escapeHTML(b.query)}</strong><small>${escapeHTML(new Date(b.created_at).toLocaleString(uiLocale()))} · ${escapeHTML(b.tool)}</small></span><span class="chip">${escapeHTML(tr(batchLabels[b.status]||b.status))}</span>${icon("arrow")}</button>`).join("")}</div>`:`<p class="download-hint">${tr("Your first batch will appear here.")}</p>`}</section>`;
  let detail="";
  if(downloadState.batchId){
    const b=await api(`/api/downloads/batch?id=${downloadState.batchId}`);
    downloadState.batch=b;
    const busy=state.summary.job?.status==="running";
    const cancel=busy&&["download","catalog"].includes(state.summary.job.type);
    detail=`<section class="section"><div class="section-heading"><div><span class="section-kicker">${tr("2 · LISTEN AND REVIEW")}</span><h2>${escapeHTML(join(b.tracks[0]?.metadata.album)||tr("Catalog preview"))}</h2><p>${b.tracks.length} ${tr("tracks ·")} ${b.tracks.filter(r=>r.matches.length).length} ${tr("with a local match ·")} ${b.tracks.filter(r=>r.status==="downloaded").length} ${tr("downloads ready")}</p></div><button class="text-button" data-action="catalog-log">${tr("Lookup log")}</button></div>${b.error?`<div class="error-box">${escapeHTML(b.error)}</div>`:""}${b.warnings.map(v=>`<div class="error-box">${escapeHTML(v)}</div>`).join("")}${!b.import?`<div class="download-panel download-batch-actions"><p>${tr("Check the credits, genre and version. Matches use tags and duration; audio fingerprint matching is not available yet.")}</p><div class="download-actions">${b.mode!=="metadata"?`<button class="button ghost" data-action="select-downloads" ${busy?"disabled":""}>${tr("Select for download")}</button><button class="button primary" data-action="fetch-downloads" ${busy?"disabled":""}>${icon("download")} ${tr("Download selection")}</button>`:""}<button class="button secondary" data-action="review-import" ${busy?"disabled":""}>${tr("Review approval")} ${icon("arrow")}</button>${cancel?`<button class="button ghost" data-action="cancel-downloads">${tr("Cancel download")}</button>`:""}</div></div>`:'<div class="review-summary">'+icon("check")+`<p><strong>${tr("Batch imported.")}</strong>${tr("The backup and undo option are in History. Prepare a new preview for further changes.")}</p></div>`}<div class="download-track-list">${b.tracks.map((r,i)=>downloadRow(b,r,i)).join("")}</div></section>`;
  }
  return `${heading(tr("DISCOVER, COMPLETE, ORGANIZE"),tr("Downloads and metadata"),tr("Full albums, new tracks and genres in your files."))}${downloadForm(info)}${list}${detail}`;
}

async function previewDownloadImport() {
  const selections=[...downloadState.choices].filter(([,v])=>v.action).map(([id,v])=>({id,...v}));
  if(!selections.length)throw new Error(tr("Choose an action for the tracks you want to approve."));
  const result=await api("/api/downloads/preview",{id:downloadState.batchId,selections:selections});
  downloadState.pending={id:downloadState.batchId,selections:selections};
  let backupSize=0;
  const items=result.operations.map(op=>{
    const row=downloadState.batch.tracks.find(r=>r.id===op.id);
    const local=row.matches.find(m=>m.file===op.file);
    if(local)backupSize+=local.size_bytes;
    const labels={add:tr("Add new file"),metadata:tr("Update metadata only"),replace:tr("Replace MP3 audio")};
    return `<li><strong>${escapeHTML(join(row.metadata.artist))} — ${escapeHTML(join(row.metadata.title))}</strong><span>${labels[op.action]} · ${escapeHTML(op.file.split("/").pop())}</span>${op.action==="add"?diffHTML(Object.fromEntries(Object.keys(fieldNames).map(k=>[k,{current:[],proposed:row.real[k]}]))):diffHTML(op.changes)}</li>`;
  }).join("");
  modal(`${modalHead(tr("Approve these additions and changes?"),`${result.operations.length} ${tr("operations selected. Only this confirmation changes your library.")}`)}<div class="modal-body"><div class="backup-note">${icon("shield")}<span>${tr("Full backup of existing files:")} <strong>${bytes(backupSize)}</strong>${tr(". Additions can also be undone: the file will be stored outside the library. Review the changes below.")}</span></div><ul class="confirm-list">${items}</ul></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Back to review")}</button><button class="button primary" data-action="confirm-import">${icon("check")} ${tr("Back up and approve")}</button></div>`);
}

async function playStaged(id) {
  const row=downloadState.batch.tracks.find(r=>r.id===id);
  if(!row?.real)throw new Error(tr("Audio is not available yet."));
  state.current={...row.real,id:`stage-${id}`};
  if(typeof setLyricsTrack==="function")setLyricsTrack(null);
  state.queue=[];
  $('#player-title').dataset.i18nUser="true";$('#player-title').textContent=join(row.real.title);
  $('#player-artist').dataset.i18nUser="true";$('#player-artist').textContent=join(row.real.artist)+" "+tr("· download preview");
  $('#player-cover').innerHTML=row.real.covers.length?`<img src="${stagedURL("cover",downloadState.batchId,id)}" alt="">`:icon("disc");
  $('#audio-quality').textContent=`MP3 · ${Math.round((row.real.bitrate_bps||0)/1000)} KBPS`;
  audio.src=stagedURL("audio",downloadState.batchId,id);
  $('#play-toggle').disabled=false;
  $('#seek').disabled=false;
  $('#previous').disabled=true;
  $('#next').disabled=true;
  await audio.play();
}

document.addEventListener("submit",async event=>{
  if(event.target.id!=="download-form")return;
  event.preventDefault();
  const submit=event.target.querySelector('[type="submit"]');
  submit.disabled=true;
  try {
    await api("/api/downloads/prepare",downloadDraft(event.target));
    await updateState();
    await render();
    toast(tr("Looking up the catalog and comparing it with your library…"));
  } catch(error){submit.disabled=false;toast(error.message);}
});

function downloadDraft(form) {
  const data=Object.fromEntries(new FormData(form));
  for(const name of ["unify_album","force_genre"])data[name]=form.elements[name].checked;
  downloadState.draft=data;
  return data;
}

document.addEventListener("input",event=>{if(event.target.closest("#download-form"))downloadDraft(event.target.closest("form"));});
document.addEventListener("change",event=>{
  const el=event.target;
  if(el.matches(".download-check")){if(el.checked)downloadState.fetchIds.add(el.dataset.id);else downloadState.fetchIds.delete(el.dataset.id);}
  if(el.matches(".import-action,.import-target")){
    const choice=downloadState.choices.get(el.dataset.id)||{};
    choice[el.matches(".import-action")?"action":"target"]=el.value;
    downloadState.choices.set(el.dataset.id,choice);
    render();
  }
});

document.addEventListener("click",async event=>{
  const button=event.target.closest("button");
  if(!button||button.disabled)return;
  try {
    if(button.dataset.batch)await openDownloadBatch(button.dataset.batch);
    else if(button.dataset.stagePlay)await playStaged(button.dataset.stagePlay);
    else if(button.dataset.downloadLog||button.dataset.action==="catalog-log"){
      const data=await api(stagedURL("log",downloadState.batchId,button.dataset.downloadLog||""));
      modal(`${modalHead(tr("Tool log"),tr("Lookup or download details."))}<div class="modal-body"><pre class="download-log">${escapeHTML(data.log)}</pre></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Close")}</button></div>`);
    } else if(button.dataset.action==="select-downloads"){
      downloadState.fetchIds=new Set(downloadState.batch.tracks.filter(r=>r.status!=="downloaded"&&(downloadState.batch.mode==="full"||!r.matches.length)).map(r=>r.id));
      document.querySelectorAll(".download-check").forEach(el=>{el.checked=downloadState.fetchIds.has(el.dataset.id);});
    } else if(button.dataset.action==="fetch-downloads"){
      if(!downloadState.fetchIds.size)throw new Error(tr("Select at least one track to download."));
      button.disabled=true;
      await api("/api/downloads/fetch",{id:downloadState.batchId,tracks:[...downloadState.fetchIds]});
      await updateState();
      await render();
      toast(tr("Download started outside your library. You can listen before approving."));
    } else if(button.dataset.action==="review-import")await previewDownloadImport();
    else if(button.dataset.action==="confirm-import"){
      button.disabled=true;
      await api("/api/downloads/import",downloadState.pending);
      $('#modal').close();
      await updateState();
      toast(tr("Creating backups and preparing approved files…"));
    } else if(button.dataset.action==="cancel-downloads"){
      await api("/api/downloads/cancel",{});
      toast(tr("Cancellation requested. Your original files are still preserved."));
    }
  } catch(error){button.disabled=false;toast(error.message);}
});
