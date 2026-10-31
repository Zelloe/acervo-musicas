"use strict";

const bulkLyricsState={data:null,initialized:null,selected:new Set(),filter:"all",query:"",page:0,pending:null,seenJob:"",jobId:null,cancelling:false,preparing:false};
const bulkLyricsLabels={synced:"Synced",plain:"Plain text",instrumental:"Instrumental",not_found:"Not found",already_synced:"Already synced",error:"Error",not_checked:"Not checked",saved:"Saved"};
const bulkReady=row=>["synced","plain"].includes(row.status)&&!row.saved;

async function loadBulkLyrics(force=false) {
  const expected=state.summary?.bulk_lyrics_id;
  if(expected===null){bulkLyricsState.data=null;bulkLyricsState.initialized=null;bulkLyricsState.selected.clear();return;}
  if(!force&&bulkLyricsState.data?.id===expected)return;
  const data=(await api("/api/lyrics/bulk")).lookup;
  bulkLyricsState.data=data;
  if(!data){bulkLyricsState.initialized=null;bulkLyricsState.selected.clear();return;}
  const ready=new Set(data.rows.filter(bulkReady).map(row=>row.id));
  if(data.complete&&bulkLyricsState.initialized!==data.id){bulkLyricsState.selected=ready;bulkLyricsState.initialized=data.id;}
  else bulkLyricsState.selected=new Set([...bulkLyricsState.selected].filter(id=>ready.has(id)));
}

function bulkRows() {
  const query=bulkLyricsState.query.normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
  return (bulkLyricsState.data?.rows||[]).filter(row=>{
    const filter=bulkLyricsState.filter;
    if(filter==="ready"&&!bulkReady(row))return false;
    if(filter==="saved"&&!row.saved)return false;
    if(!["all","ready","saved"].includes(filter)&&(row.saved||row.status!==filter))return false;
    return !query||`${join(row.title)} ${join(row.artist)} ${join(row.album)} ${row.file}`.normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase().includes(query);
  });
}

function updateBulkSelection() {
  const rows=bulkLyricsState.data?.rows||[];
  const selected=rows.filter(row=>bulkReady(row)&&bulkLyricsState.selected.has(row.id)).length;
  const running=state.summary?.job?.status==="running";
  const busy=state.serverIncompatible||running||!!state.summary?.lyrics_busy||bulkLyricsState.preparing;
  document.querySelectorAll('[data-bulk-selected]').forEach(node=>node.textContent=number(selected));
  document.querySelectorAll('[data-action="bulk-review"]').forEach(button=>button.disabled=busy||!!state.summary?.protection_error||!selected);
  document.querySelectorAll('.bulk-lyrics-check[data-ready="true"], [data-action="bulk-select-ready"], [data-action="bulk-clear"]').forEach(node=>node.disabled=busy);
  const find=$('[data-action="bulk-find"]');if(find)find.disabled=busy||!state.summary?.summary?.read_count;
  const cancel=$('[data-action="bulk-cancel"]');
  if(cancel)cancel.disabled=bulkLyricsState.cancelling||!running||state.summary.job.type!=="lyrics_lookup";
}

function bulkResultsHTML() {
  const rows=bulkRows(),size=40;
  bulkLyricsState.page=Math.min(bulkLyricsState.page,Math.max(0,Math.ceil(rows.length/size)-1));
  const first=bulkLyricsState.page*size,visible=rows.slice(first,first+size);
  return `${visible.length?`<div class="bulk-lyrics-list" role="list">${visible.map(row=>{
    const ready=bulkReady(row),label=row.saved?"Saved":bulkLyricsLabels[row.status]||"Error";
    const current={none:"No lyrics in file",plain:"Plain lyrics in file",synced:"Synced lyrics in file"}[row.current]||"No lyrics in file";
    return `<article class="bulk-lyrics-row" role="listitem"><input class="check bulk-lyrics-check" type="checkbox" data-bulk-id="${escapeHTML(row.id)}" data-ready="${ready}" aria-label="${tr("Select lyrics for")} ${escapeHTML(join(row.title))}" ${bulkLyricsState.selected.has(row.id)?"checked":""} ${ready?"":"disabled"}><div class="bulk-lyrics-track"><strong>${escapeHTML(join(row.title))}</strong><span>${escapeHTML(join(row.artist))}</span><small>${escapeHTML(tr(current))}</small>${row.error?`<p class="download-warning">${escapeHTML(row.error)}</p>`:""}</div><div class="bulk-lyrics-actions"><span class="chip ${row.status==="error"?"amber":"neutral"}">${escapeHTML(tr(label))}</span>${row.text?`<button class="text-button" data-bulk-preview="${escapeHTML(row.id)}">${tr("Preview")}</button>`:row.status==="already_synced"?`<button class="text-button" data-lyrics="${escapeHTML(row.id)}">${tr("View lyrics")}</button>`:""}</div></article>`;
  }).join("")}</div>`:empty(tr("No lyrics in this filter"),tr("Choose another status or change the search."),"lyrics")}<div class="table-footer"><span>${rows.length?`${number(first+1)}–${number(Math.min(first+size,rows.length))} ${tr("of")} ${number(rows.length)}`:tr("0 tracks")}</span><div class="pagination"><button class="button small ghost" data-bulk-page="${bulkLyricsState.page-1}" ${bulkLyricsState.page===0?"disabled":""}>${tr("Previous")}</button><button class="button small ghost" data-bulk-page="${bulkLyricsState.page+1}" ${first+size>=rows.length?"disabled":""}>${tr("Next")}</button></div></div>`;
}

function refreshBulkResults() {
  const target=$('#bulk-lyrics-results');if(target)target.innerHTML=bulkResultsHTML();
  updateBulkSelection();
}

async function renderBulkLyrics() {
  await loadBulkLyrics();
  const data=bulkLyricsState.data,rows=data?.rows||[];
  const job=state.summary.job;
  if(job?.type==="lyrics_lookup")bulkLyricsState.jobId=job.id;
  const running=job?.type==="lyrics_lookup"&&job.status==="running";
  const counts={all:rows.length,ready:rows.filter(bulkReady).length,saved:rows.filter(row=>row.saved).length};
  for(const status of Object.keys(bulkLyricsLabels))if(status!=="saved")counts[status]=rows.filter(row=>!row.saved&&row.status===status).length;
  const filters=[["all","All"],["ready","Ready to save"],["synced","Synced"],["plain","Plain text"],["already_synced","Already synced"],["saved","Saved"],["instrumental","Instrumental"],["not_found","Not found"],["error","Error"],["not_checked","Not checked"]];
  const action=running?`<button class="button ghost" data-action="bulk-cancel">${tr("Stop lookup")}</button>`:`<button class="button primary" data-action="bulk-find" ${state.summary.job?.status==="running"||!state.summary.summary.read_count?"disabled":""}>${icon("search")} ${tr(data?"Look up again":"Find lyrics for all tracks")}</button>`;
  const controls=`<div class="bulk-lyrics-toolbar"><div class="bulk-lyrics-selection"><strong><span data-bulk-selected>0</span> ${tr("selected")}</strong><button class="text-button" data-action="bulk-select-ready">${tr("Select ready lyrics")}</button><button class="text-button" data-action="bulk-clear">${tr("Clear selection")}</button></div><button class="button primary" data-action="bulk-review">${icon("check")} ${tr("Review and save")} (<span data-bulk-selected>0</span>)</button></div>`;
  return `${heading(tr("LYRICS"),tr("Lyrics for all tracks"),tr("Find lyrics in the background, review the list, then save the selected lyrics with one backup and undo."),`<button class="button ghost" data-view="library">${tr("Back to library")}</button>${action}`)}<section class="download-panel"><p class="download-hint">${tr("The lookup reads your files and sends only track details to LRCLIB. Nothing is written to your music until you confirm.")}</p>${running?`<p class="bulk-lyrics-progress" role="status">${tr("Lookup in progress. You can keep listening while it runs.")}</p>`:""}${data?.cancelled?`<p class="download-hint">${tr("Lookup stopped. Review the available results or run it again.")}</p>`:""}${data?.error?`<div class="error-box">${escapeHTML(data.error)}</div>`:""}${data?.complete?`${controls}<p class="download-hint">${tr("Selection is kept across pages and filters; only checked lyrics will be saved.")}</p><div class="bulk-lyrics-filters" role="group" aria-label="${tr("Lyrics status")}">${filters.filter(([key])=>key==="all"||key==="ready"||counts[key]).map(([key,label])=>`<button class="chip-button ${bulkLyricsState.filter===key?"active":""}" data-bulk-filter="${key}" aria-pressed="${bulkLyricsState.filter===key}">${escapeHTML(tr(label))} <span>${number(counts[key])}</span></button>`).join("")}</div><label class="bulk-lyrics-search">${tr("Filter these results")}<input class="field" id="bulk-lyrics-search" type="search" value="${escapeHTML(bulkLyricsState.query)}" placeholder="${tr("Title, artist, album or file")}"></label><div id="bulk-lyrics-results">${bulkResultsHTML()}</div>`:empty(running?tr("Finding lyrics…"):tr("Ready to find lyrics"),tr("Synced and plain lyrics will be selected for review. Tracks already synchronized in the file are kept."),"lyrics")}</section>`;
}

async function startBulkLyrics(button) {
  button.disabled=true;
  try{
    await api("/api/lyrics/bulk/start",{});
    bulkLyricsState.data=null;bulkLyricsState.initialized=null;bulkLyricsState.selected.clear();bulkLyricsState.filter="all";bulkLyricsState.query="";bulkLyricsState.page=0;bulkLyricsState.cancelling=false;
    await updateState();await navigate("lyrics");
  }catch(error){toast(error.message);button.disabled=false;}
}

function previewBulkRow(id) {
  const row=bulkLyricsState.data?.rows.find(row=>row.id===id);if(!row?.text)return;
  modal(`${modalHead(tr("Found lyrics"),escapeHTML(join(row.artist)+" — "+join(row.title)))}<div class="modal-body"><span class="chip">${tr(row.status==="synced"?"Synced (LRC)":"Plain text")}</span><pre class="lyrics-review">${escapeHTML(row.text)}</pre><p class="source">${escapeHTML(row.file)}</p></div><div class="modal-actions"><button class="button primary" data-action="close-modal">${tr("Back to the list")}</button></div>`);
}

async function reviewBulkLyrics() {
  const ids=(bulkLyricsState.data?.rows||[]).filter(row=>bulkReady(row)&&bulkLyricsState.selected.has(row.id)).map(row=>row.id);
  if(!ids.length)return;
  bulkLyricsState.preparing=true;updateBulkSelection();
  try{
    const preview=await api("/api/lyrics/bulk/preview",{lookup:bulkLyricsState.data.id,ids});
    bulkLyricsState.pending=preview;
    modal(`${modalHead(tr("Save the selected lyrics?"),`${number(preview.count)} ${tr("tracks with a full backup and one undo.")}`)}<div class="modal-body"><div class="backup-note">${icon("shield")}<span>${tr("Only the selected lyrics will be written. Timecodes stay inside each file, and History can restore the whole batch.")}</span></div><dl class="details-list"><div><dt>${tr("Full backup")}</dt><dd>${escapeHTML(bytes(preview.size_bytes))}</dd></div></dl>${preview.skipped?`<p class="download-hint">${number(preview.skipped)} ${tr("tracks already match and will be skipped.")}</p>`:""}<ul class="confirm-list">${preview.sample.map(row=>`<li><strong>${escapeHTML(join(row.title))}</strong><span>${escapeHTML(join(row.artist))} · ${escapeHTML(tr(row.format==="synced"?"Synced (LRC)":"Plain text"))}</span></li>`).join("")}</ul>${preview.count>preview.sample.length?`<p class="download-hint">${tr("And")} ${number(preview.count-preview.sample.length)} ${tr("more selected tracks.")}</p>`:""}</div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Back to the list")}</button><button class="button primary" data-action="bulk-confirm">${tr("Back up and save selected lyrics")}</button></div>`);
  }catch(error){toast(error.message);}finally{bulkLyricsState.preparing=false;updateBulkSelection();}
}

async function captureBulkLyricsJob(job) {
  if(!job)return false;
  let newLookup=false;
  if(job.type==="lyrics_lookup"&&job.id!==bulkLyricsState.jobId){bulkLyricsState.jobId=job.id;bulkLyricsState.data=null;bulkLyricsState.initialized=null;bulkLyricsState.selected.clear();bulkLyricsState.cancelling=false;newLookup=true;}
  const key=`${job.id}:${job.status}`;
  if(job.status==="running")return newLookup;
  if(bulkLyricsState.seenJob===key)return false;
  if(job.type==="lyrics_lookup"){
    bulkLyricsState.seenJob=key;bulkLyricsState.cancelling=false;
    await loadBulkLyrics(true);
    if(job.status==="completed")toast(tr(job.result?.cancelled?"Lookup stopped. Review the available results or run it again.":"Lyrics lookup finished. Review the selected results."));
    return true;
  }
  if(job.status==="completed"&&Array.isArray(job.result?.lyrics_track_ids)){
    bulkLyricsState.seenJob=key;await loadBulkLyrics(true);
    toast(tr("Selected lyrics saved. You can undo this in History."));return true;
  }
  return false;
}

document.addEventListener("click",async event=>{
  const button=event.target.closest("button");if(!button||button.disabled)return;
  try{
    if(button.dataset.bulkPreview){previewBulkRow(button.dataset.bulkPreview);return;}
    if(button.dataset.bulkFilter){bulkLyricsState.filter=button.dataset.bulkFilter;bulkLyricsState.page=0;await render();return;}
    if(button.dataset.bulkPage!==undefined){bulkLyricsState.page=Math.max(0,Number(button.dataset.bulkPage));refreshBulkResults();$('#bulk-lyrics-results')?.scrollIntoView({block:"start",behavior:"auto"});return;}
    const action=button.dataset.action;
    if(action==="bulk-find")await startBulkLyrics(button);
    else if(action==="bulk-cancel"){
      bulkLyricsState.cancelling=true;updateBulkSelection();
      try{await api("/api/lyrics/bulk/cancel",{});toast(tr("Cancellation requested. The current lookup will finish first."));}catch(error){bulkLyricsState.cancelling=false;throw error;}
    }else if(action==="bulk-select-ready"){bulkLyricsState.selected=new Set((bulkLyricsState.data?.rows||[]).filter(bulkReady).map(row=>row.id));refreshBulkResults();}
    else if(action==="bulk-clear"){bulkLyricsState.selected.clear();refreshBulkResults();}
    else if(action==="bulk-review")await reviewBulkLyrics();
    else if(action==="bulk-confirm"&&bulkLyricsState.pending){
      button.disabled=true;
      try{await api("/api/lyrics/bulk/save",{preview:bulkLyricsState.pending.preview});bulkLyricsState.pending=null;$('#modal').close();await updateState();await captureBulkLyricsJob(state.summary.job);await render();}catch(error){button.disabled=false;throw error;}
    }
  }catch(error){toast(error.message);updateBulkSelection();}
});
document.addEventListener("change",event=>{
  if(!event.target.matches('.bulk-lyrics-check'))return;
  if(event.target.checked)bulkLyricsState.selected.add(event.target.dataset.bulkId);else bulkLyricsState.selected.delete(event.target.dataset.bulkId);
  updateBulkSelection();
});
document.addEventListener("input",event=>{if(event.target.id==="bulk-lyrics-search"){bulkLyricsState.query=event.target.value;bulkLyricsState.page=0;refreshBulkResults();}});
