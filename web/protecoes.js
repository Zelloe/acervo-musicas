"use strict";

const protectionState = {albumId:null};

function protectionControl(track, field) {
  if(track.erro_protecoes)return '<span class="source">Estado da proteção indisponível</span>';
  const enabled = (track.campos_protegidos || []).includes(field);
  return `<button class="text-button" type="button" data-track-protection="${escapeHTML(track.id)}" data-field="${field}" data-enable="${!enabled}" aria-pressed="${enabled}" ${state.summary.job?.estado==="executando"?"disabled":""}>${icon(enabled?"lock":"shield")} ${enabled?"Protegido · desbloquear":"Proteger campo"}</button>`;
}

function showProtectionError(info) {
  if(!info.erro_protecoes)return false;
  modal(`${modalHead("Não foi possível ler as proteções","A consulta e a reprodução da biblioteca continuam disponíveis.")}<div class="modal-body"><div class="error-box">${escapeHTML(info.erro_protecoes)}</div><p class="download-hint">Confira o arquivo de preferências indicado. Alterações ficam bloqueadas até que ele possa ser lido novamente.</p></div><div class="modal-actions"><button class="button primary" data-action="close-modal">Fechar</button></div>`);
  return true;
}

async function showAlbumProtections(albumId) {
  const info = await api(`/api/protections?${new URLSearchParams({album_id:albumId})}`);
  if(showProtectionError(info))return;
  const album = info.album;
  protectionState.albumId = albumId;
  modal(`${modalHead("Proteger campos do álbum",`${escapeHTML(join(album.titulo))} · ${escapeHTML(join(album.artista))} · ${number(album.faixas)} faixas`)}<div class="modal-body"><div class="backup-note">${icon("lock")}<span>Campos protegidos mantêm suas escolhas, mesmo quando estão vazios. A ação vale para as faixas atuais deste álbum e fica nas preferências locais até você desbloquear. Novas faixas precisam de proteção própria.</span></div>${info.campos.map(field=>`<div class="form-row"><label><input class="check album-protection-field" type="checkbox" value="${field}"> ${escapeHTML(fieldNames[field]||field)}</label><p>${number(album.campos[field])} de ${number(album.faixas)} faixas com este campo protegido</p></div>`).join("")}</div><div class="modal-actions"><button class="button ghost" data-action="close-modal">Fechar</button><button class="button ghost" data-action="unprotect-album">Desbloquear</button><button class="button primary" data-action="protect-album">${icon("lock")} Proteger</button></div>`);
}

async function showProtections() {
  const info = await api("/api/protections");
  if(showProtectionError(info))return;
  modal(`${modalHead("Campos protegidos",`${number(info.protecoes.length)} faixas com escolhas preservadas.`)}<div class="modal-body">${info.protecoes.length?`<ul class="confirm-list">${info.protecoes.map(row=>`<li><strong>${escapeHTML(join(row.titulo)||row.arquivo.split("/").pop())}</strong><span>${escapeHTML(join(row.artista)||row.arquivo)}</span><span>${row.campos.map(f=>escapeHTML(fieldNames[f]||f)).join(" · ")}</span>${row.id?`<button class="text-button" data-info="${escapeHTML(row.id)}">${icon("lock")} Ver campos e desbloquear</button>`:'<span>Faixa fora do inventário atual. Atualize a biblioteca para conferir o caminho.</span>'}</li>`).join("")}</ul>`:'<p class="download-hint">Nenhum campo protegido. Abra os detalhes de uma faixa ou use o cadeado de um álbum para preservar suas escolhas.</p>'}</div><div class="modal-actions"><button class="button primary" data-action="close-modal">Fechar</button></div>`);
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
      await api("/api/protections",{ids:[button.dataset.trackProtection],campos:[button.dataset.field],proteger:enabled});
      await refreshAfterProtection();
      await showDetails(button.dataset.trackProtection);
      toast(enabled?"Campo protegido. O Acervo preservará esse valor.":"Campo desbloqueado. Atualize a análise para gerar novas sugestões.");
    } else if(button.dataset.protectionAlbum){
      await showAlbumProtections(button.dataset.protectionAlbum);
    } else if(button.dataset.action==="protection-list"){
      await showProtections();
    } else if(["protect-album","unprotect-album"].includes(button.dataset.action)){
      const fields=[...document.querySelectorAll('.album-protection-field:checked')].map(el=>el.value);
      if(!fields.length)throw new Error("Selecione os campos que deseja proteger ou desbloquear.");
      button.disabled=true;
      const enabled=button.dataset.action==="protect-album";
      await api("/api/protections",{album_id:protectionState.albumId,campos:fields,proteger:enabled});
      await refreshAfterProtection();
      await showAlbumProtections(protectionState.albumId);
      toast(enabled?"Campos protegidos em todas as faixas deste álbum.":"Campos desbloqueados nas faixas deste álbum.");
    }
  } catch(error){button.disabled=false;toast(error.message);}
});
