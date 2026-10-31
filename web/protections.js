"use strict";

const protectionState = {albumId:null};

function protectionControl(track, field) {
  if(track.protection_error)return `<span class="source">${tr("Protection status unavailable")}</span>`;
  const enabled = (track.protected_fields || []).includes(field);
  return `<button class="text-button" type="button" data-track-protection="${escapeHTML(track.id)}" data-field="${field}" data-enable="${!enabled}" aria-pressed="${enabled}" ${state.summary.job?.status==="running"?"disabled":""}>${icon(enabled?"lock":"shield")} ${enabled?tr("Protected · unlock"):tr("Protect field")}</button>`;
}

function showProtectionError(info) {
  if(!info.protection_error)return false;
  modal(`${modalHead(tr("Could not read field protections"),tr("You can still browse and play your library."))}<div class="modal-body"><div class="error-box">${escapeHTML(info.protection_error)}</div><p class="download-hint">${tr("Check the preferences file shown above. Changes remain blocked until it can be read again.")}</p></div><div class="modal-actions"><button class="button primary" data-action="close-modal">${tr("Close")}</button></div>`);
  return true;
}

async function showAlbumProtections(albumId) {
  const info = await api(`/api/protections?${new URLSearchParams({album_id:albumId})}`);
  if(showProtectionError(info))return;
  const album = info.album;
  protectionState.albumId = albumId;
  modal(`${modalHead(tr("Protect album fields"),`${escapeHTML(join(album.title))} · ${escapeHTML(join(album.artist))} · ${number(album.tracks)} ${tr("tracks")}`)}<div class="modal-body"><div class="backup-note">${icon("lock")}<span>${tr("Protected fields preserve your choices, even when empty. This applies to the album's current tracks and remains in local preferences until unlocked. New tracks need their own protection.")}</span></div>${info.fields.map(field=>`<div class="form-row"><label><input class="check album-protection-field" type="checkbox" value="${field}"> ${escapeHTML(tr(fieldNames[field]||field))}</label><p>${number(album.fields[field])} ${tr("of")} ${number(album.tracks)} ${tr("tracks with this field protected")}</p></div>`).join("")}</div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Close")}</button><button class="button ghost" data-action="unprotect-album">${tr("Unlock")}</button><button class="button primary" data-action="protect-album">${icon("lock")} ${tr("Protect")}</button></div>`);
}

async function showProtections() {
  const info = await api("/api/protections");
  if(showProtectionError(info))return;
  modal(`${modalHead(tr("Protected fields"),`${number(info.protections.length)} ${tr("tracks with preserved choices.")}`)}<div class="modal-body">${info.protections.length?`<ul class="confirm-list">${info.protections.map(row=>`<li><strong>${escapeHTML(join(row.title)||row.file.split("/").pop())}</strong><span>${escapeHTML(join(row.artist)||row.file)}</span><span>${row.fields.map(f=>escapeHTML(tr(fieldNames[f]||f))).join(" · ")}</span>${row.id?`<button class="text-button" data-info="${escapeHTML(row.id)}">${icon("lock")} ${tr("View fields and unlock")}</button>`:`<span>${tr("Track is outside the current inventory. Refresh the library to check its path.")}</span>`}</li>`).join("")}</ul>`:`<p class="download-hint">${tr("No fields protected. Open a track's details or use an album's lock button to preserve your choices.")}</p>`}</div><div class="modal-actions"><button class="button primary" data-action="close-modal">${tr("Close")}</button></div>`);
}

async function refreshAfterProtection() {
  state.proposals = null;
  state.selected.clear();
  await updateState();
  await render();
}

document.addEventListener("click",async event=>{
  const button = event.target.closest("button");
  if(!button || button.disabled)return;
  try {
    if(button.dataset.trackProtection){
      button.disabled=true;
      const enabled=button.dataset.enable==="true";
      await api("/api/protections",{ids:[button.dataset.trackProtection],fields:[button.dataset.field],protect:enabled});
      await refreshAfterProtection();
      await showDetails(button.dataset.trackProtection);
      toast(enabled?tr("Field protected. Acervo will preserve this value."):tr("Field unlocked. Scan the library again for new suggestions."));
    } else if(button.dataset.protectionAlbum){
      await showAlbumProtections(button.dataset.protectionAlbum);
    } else if(button.dataset.action==="protection-list"){
      await showProtections();
    } else if(["protect-album","unprotect-album"].includes(button.dataset.action)){
      const fields=[...document.querySelectorAll('.album-protection-field:checked')].map(el=>el.value);
      if(!fields.length)throw new Error(tr("Select the fields to protect or unlock."));
      button.disabled=true;
      const enabled=button.dataset.action==="protect-album";
      await api("/api/protections",{album_id:protectionState.albumId,fields:fields,protect:enabled});
      await refreshAfterProtection();
      await showAlbumProtections(protectionState.albumId);
      toast(enabled?tr("Fields protected for all tracks in this album."):tr("Fields unlocked for this album's tracks."));
    }
  } catch(error){button.disabled=false;toast(error.message);}
});
