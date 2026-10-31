"use strict";

const downloadState = {batchId:null, batch:null, fetchIds:new Set(), choices:new Map(), draft:{}, pending:null};
const fieldNames = {title:"Título",artist:"Artista",album_artist:"Artista do álbum",album:"Álbum",genre:"Gênero",year:"Ano",track:"Faixa",disc:"Disco"};
const batchLabels = {querying:"Consultando",ready:"Catálogo pronto",downloading:"Baixando",review:"Pronto para revisão",cancelled:"Cancelado",error:"Precisa de atenção",imported:"Importado"};
const stagedURL = (kind,batch,row) => `/api/downloads/${kind}?${new URLSearchParams({id:batch,track:row})}`;

function downloadForm(info) {
  const draft=downloadState.draft;
  const input=(name,label,placeholder="")=>`<label>${label}<input class="field" name="${name}" value="${escapeHTML(draft[name]||"")}" placeholder="${escapeHTML(placeholder)}"></label>`;
  const select=(name,label,items)=>`<label>${label}<select class="field" name="${name}">${items.map(([v,t])=>`<option value="${escapeHTML(v)}" ${draft[name]===v?"selected":""}>${escapeHTML(t)}</option>`).join("")}</select></label>`;
  const busy=state.summary.job?.status==="running";
  return `<form id="download-form" class="download-panel"><div class="section-heading"><div><h2>${tr("Mais música no seu acervo.")}</h2><p>${tr("Comece pelo catálogo. Você decide o que entra na coleção.")}</p></div><span class="chip">${tr("1 · Preparar")}</span></div><div class="download-form-grid"><label class="wide">${tr("Link ou busca")}<input class="field" name="query" required maxlength="1200" placeholder="${tr("Link do Spotify, YouTube ou artista e álbum")}" value="${escapeHTML(draft.query||"")}"></label>${select("tool",tr("Ferramenta"),[["spotdl",tr("spotDL · catálogo Spotify e áudio YouTube")],["yt-dlp",tr("yt-dlp · link direto do YouTube")]])}${select("mode",tr("O que deseja fazer?"),[["missing",tr("Baixar apenas o que estiver faltando")],["full",tr("Preparar um álbum completo novamente")],["metadata",tr("Unificar só os metadados existentes")]])}${input("album",tr("Nome unificado do álbum"),tr("Manter o nome da referência"))}${input("genre",tr("Gênero"),tr("Sugerir pela fonte e pelas regras locais"))}${input("artist",tr("Artista"),tr("Usar os créditos da fonte"))}${input("album_artist",tr("Artista do álbum"),tr("Usar o artista principal"))}${input("year",tr("Ano / data"),tr("AAAA ou AAAA-MM-DD"))}${select("bitrate",tr("MP3 · taxa de saída"),[["auto",tr("Automática")],["320k","320 kbps"],["256k","256 kbps"],["192k","192 kbps"],["128k","128 kbps"]])}${select("destination",tr("Pasta existente para novas faixas"),info.folders.map(p=>[p,p.replace(/^\/home\/[^/]+/,"~")]))}</div><div class="download-options"><label><input class="check" type="checkbox" name="unify_album" ${draft.unify_album!==false?"checked":""}> ${tr("Sugerir o nome base para edições remasterizadas / deluxe")}</label><label><input class="check" type="checkbox" name="force_genre" ${draft.force_genre?"checked":""}> ${tr("Propor também a troca de gêneros já preenchidos")}</label></div><p class="download-hint">${tr("O spotDL usa o Spotify como catálogo e busca o áudio no YouTube. A taxa de saída não melhora a qualidade da fonte. Novos arquivos serão MP3; um FLAC existente pode receber tags, mas não será substituído por MP3.")}</p><div class="download-tools">${Object.entries(info.tools).filter(([k])=>k!=="deno").map(([k,v])=>`<span class="chip ${v.available?"":"amber"}">${escapeHTML(k)} · ${v.available?tr("disponível"):tr("não instalado")}</span>`).join("")}</div><div class="download-actions"><button class="button primary" type="submit" ${busy?"disabled":""}>${tr("Consultar e comparar")} ${icon("arrow")}</button></div></form>`;
}

function selectedLocal(row,choice) {
  return row.matches.find(m=>m.id===choice?.target) || (row.matches.length===1?row.matches[0]:null);
}

function diffHTML(changes) {
  return Object.entries(changes||{}).map(([field,c])=>`<div class="download-diff"><span>${escapeHTML(tr(fieldNames[field]||field))}</span><span class="change-old">${escapeHTML(join(c.current)||tr("Não definido"))}</span><span class="change-new">${icon("arrow")}${escapeHTML(join(c.proposed))}</span></div>`).join("");
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
  const options=[['',tr("Manter a biblioteca como está")]];
  if(row.matches.some(m=>Object.keys(m.changes).length))options.push(["metadata",tr("Aplicar somente as tags sugeridas")]);
  if(available)options.push(["add",row.matches.length?tr("Adicionar como outra versão"):tr("Adicionar à biblioteca")]);
  if(available && row.matches.some(m=>m.format==="MP3"))options.push(["replace",tr("Substituir um MP3 · com backup")]);
  const changes=choice.action==="metadata"?local?.changes:choice.action==="replace"?local?.replacement_changes:null;
  return `<article class="download-track" data-row="${row.id}"><div class="download-track-head"><div class="download-cover">${art}</div><div class="download-title"><span class="section-kicker">${tr("FAIXA")} ${escapeHTML(join(metadata.track)||index+1)} ${tr("· DISCO")} ${escapeHTML(join(metadata.disc))}</span><h3>${escapeHTML(join(metadata.title))}</h3><p>${escapeHTML(join(metadata.artist))} · ${escapeHTML(join(metadata.album)||tr("Álbum não informado"))}</p></div><span class="chip ${row.status==="error"?"amber":""}">${available?tr("Áudio pronto"):row.matches.length?`${row.matches.length} ${tr("versão local")}`:row.status==="error"?tr("Falhou"):tr("Nova faixa")}</span></div><div class="download-tags"><span>${icon("tag")}${escapeHTML(join(metadata.genre)||tr("Gênero pendente"))}</span><span>${escapeHTML(join(metadata.year)||tr("Ano pendente"))}</span><span>${duration(row.source.duration)}</span></div><p class="source">${tr("Gênero:")} ${escapeHTML(row.genre_source)}</p>${row.matches.map(m=>`<div class="local-match"><span class="format ${m.format==="FLAC"?"flac":""}">${m.format}</span><span>${escapeHTML(m.file.split("/").pop())}</span><span>${duration(m.duration_seconds)}</span></div>`).join("")}${[...new Set(row.warnings)].map(v=>`<p class="download-warning">${escapeHTML(v)}</p>`).join("")}${row.error?`<p class="download-warning">${escapeHTML(row.error)}</p>`:""}<div class="download-row-actions">${canFetch?`<label><input class="check download-check" type="checkbox" data-id="${row.id}" ${downloadState.fetchIds.has(row.id)?"checked":""} ${busy?"disabled":""}> ${tr("Baixar para revisão")}</label>`:""}${available?`<button class="button small ghost" data-stage-play="${row.id}">${icon("play")} ${tr("Ouvir download")}</button>`:""}${row.status==="error"?`<button class="text-button" data-download-log="${row.id}">${tr("Ver registro")}</button>`:""}</div>${!imported?`<label class="import-choice">${tr("Ao aprovar")}<select class="field import-action" data-id="${row.id}" ${busy?"disabled":""}>${options.map(([value,label])=>`<option value="${value}" ${choice.action===value?"selected":""}>${label}</option>`).join("")}</select></label>${choice.action&&choice.action!=="add"&&row.matches.length>1?`<label class="import-choice">${tr("Arquivo local a alterar")}<select class="field import-target" data-id="${row.id}"><option value="">${tr("Escolha a versão…")}</option>${row.matches.map(m=>`<option value="${m.id}" ${choice.target===m.id?"selected":""}>${escapeHTML(m.format+" · "+m.file.split("/").pop())}</option>`).join("")}</select></label>`:""}${changes?`<div class="download-diffs">${diffHTML(changes)}</div>`:""}${choice.action==="add"?`<p class="source">${tr("Novo arquivo:")} ${escapeHTML(row.new_file.split("/").pop())}</p>`:""}`:""}</article>`;
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
  const list=`<section class="download-panel"><div class="section-heading"><div><h2>${tr("Seus lotes de revisão")}</h2><p>${tr("Downloads permanecem fora da coleção até a aprovação.")}</p></div></div>${info.batches.length?`<div class="batch-list">${info.batches.map(b=>`<button class="batch-item ${downloadState.batchId===b.id?"active":""}" data-batch="${b.id}"><span>${icon("disc")}</span><span><strong>${escapeHTML(b.query)}</strong><small>${escapeHTML(new Date(b.created_at).toLocaleString(uiLocale()))} · ${escapeHTML(b.tool)}</small></span><span class="chip">${escapeHTML(tr(batchLabels[b.status]||b.status))}</span>${icon("arrow")}</button>`).join("")}</div>`:`<p class="download-hint">${tr("Seu primeiro lote aparecerá aqui.")}</p>`}</section>`;
  let detail="";
  if(downloadState.batchId){
    const b=await api(`/api/downloads/batch?id=${downloadState.batchId}`);
    downloadState.batch=b;
    const busy=state.summary.job?.status==="running";
    const cancel=busy&&["download","catalog"].includes(state.summary.job.type);
    detail=`<section class="section"><div class="section-heading"><div><span class="section-kicker">${tr("2 · OUVIR E REVISAR")}</span><h2>${escapeHTML(join(b.tracks[0]?.metadata.album)||tr("Prévia do catálogo"))}</h2><p>${b.tracks.length} ${tr("faixas ·")} ${b.tracks.filter(r=>r.matches.length).length} ${tr("com correspondência local ·")} ${b.tracks.filter(r=>r.status==="downloaded").length} ${tr("downloads prontos")}</p></div><button class="text-button" data-action="catalog-log">${tr("Registro da consulta")}</button></div>${b.error?`<div class="error-box">${escapeHTML(b.error)}</div>`:""}${b.warnings.map(v=>`<div class="error-box">${escapeHTML(v)}</div>`).join("")}${!b.import?`<div class="download-panel download-batch-actions"><p>${tr("Confira os créditos, o gênero e a versão. As correspondências usam tags e duração; ainda não há confirmação por impressão digital.")}</p><div class="download-actions">${b.mode!=="metadata"?`<button class="button ghost" data-action="select-downloads" ${busy?"disabled":""}>${tr("Selecionar para download")}</button><button class="button primary" data-action="fetch-downloads" ${busy?"disabled":""}>${icon("download")} ${tr("Baixar seleção")}</button>`:""}<button class="button secondary" data-action="review-import" ${busy?"disabled":""}>${tr("Revisar aprovação")} ${icon("arrow")}</button>${cancel?`<button class="button ghost" data-action="cancel-downloads">${tr("Cancelar download")}</button>`:""}</div></div>`:'<div class="review-summary">'+icon("check")+`<p><strong>${tr("Lote importado.")}</strong>${tr("O backup e a opção de desfazer estão no Histórico. Para outras alterações, prepare uma nova prévia.")}</p></div>`}<div class="download-track-list">${b.tracks.map((r,i)=>downloadRow(b,r,i)).join("")}</div></section>`;
  }
  return `${heading(tr("DESCUBRA, COMPLETE, UNIFIQUE"),tr("Downloads e metadados"),tr("Álbuns inteiros, faixas novas e gêneros nos seus arquivos."))}${downloadForm(info)}${list}${detail}`;
}

async function previewDownloadImport() {
  const selections=[...downloadState.choices].filter(([,v])=>v.action).map(([id,v])=>({id,...v}));
  if(!selections.length)throw new Error(tr("Escolha a ação das faixas que deseja aprovar."));
  const result=await api("/api/downloads/preview",{id:downloadState.batchId,selections:selections});
  downloadState.pending={id:downloadState.batchId,selections:selections};
  let backupSize=0;
  const items=result.operations.map(op=>{
    const row=downloadState.batch.tracks.find(r=>r.id===op.id);
    const local=row.matches.find(m=>m.file===op.file);
    if(local)backupSize+=local.size_bytes;
    const labels={add:tr("Adicionar novo arquivo"),metadata:tr("Atualizar somente metadados"),replace:tr("Substituir o áudio do MP3")};
    return `<li><strong>${escapeHTML(join(row.metadata.artist))} — ${escapeHTML(join(row.metadata.title))}</strong><span>${labels[op.action]} · ${escapeHTML(op.file.split("/").pop())}</span>${op.action==="add"?diffHTML(Object.fromEntries(Object.keys(fieldNames).map(k=>[k,{current:[],proposed:row.real[k]}]))):diffHTML(op.changes)}</li>`;
  }).join("");
  modal(`${modalHead(tr("Aprovar a entrada na coleção?"),`${result.operations.length} ${tr("operações selecionadas. Só esta confirmação modifica sua biblioteca.")}`)}<div class="modal-body"><div class="backup-note">${icon("shield")}<span>${tr("Backup completo dos arquivos existentes:")} <strong>${bytes(backupSize)}</strong>${tr(". As adições também podem ser desfeitas: o arquivo será guardado fora da biblioteca. Confira as mudanças abaixo.")}</span></div><ul class="confirm-list">${items}</ul></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Voltar à revisão")}</button><button class="button primary" data-action="confirm-import">${icon("check")} ${tr("Criar backup e aprovar")}</button></div>`);
}

async function playStaged(id) {
  const row=downloadState.batch.tracks.find(r=>r.id===id);
  if(!row?.real)throw new Error(tr("Áudio ainda não disponível."));
  state.current={...row.real,id:`stage-${id}`};
  if(typeof setLyricsTrack==="function")setLyricsTrack(null);
  state.queue=[];
  $('#player-title').dataset.i18nUser="true";$('#player-title').textContent=join(row.real.title);
  $('#player-artist').dataset.i18nUser="true";$('#player-artist').textContent=join(row.real.artist)+" "+tr("· prévia do download");
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
    toast(tr("Consultando o catálogo e comparando com a biblioteca…"));
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
      modal(`${modalHead(tr("Registro da ferramenta"),tr("Detalhes da consulta ou download."))}<div class="modal-body"><pre class="download-log">${escapeHTML(data.log)}</pre></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Fechar")}</button></div>`);
    } else if(button.dataset.action==="select-downloads"){
      downloadState.fetchIds=new Set(downloadState.batch.tracks.filter(r=>r.status!=="downloaded"&&(downloadState.batch.mode==="full"||!r.matches.length)).map(r=>r.id));
      document.querySelectorAll(".download-check").forEach(el=>{el.checked=downloadState.fetchIds.has(el.dataset.id);});
    } else if(button.dataset.action==="fetch-downloads"){
      if(!downloadState.fetchIds.size)throw new Error(tr("Selecione ao menos uma faixa para baixar."));
      button.disabled=true;
      await api("/api/downloads/fetch",{id:downloadState.batchId,tracks:[...downloadState.fetchIds]});
      await updateState();
      await render();
      toast(tr("Download iniciado fora da biblioteca. Você poderá ouvir antes de aprovar."));
    } else if(button.dataset.action==="review-import")await previewDownloadImport();
    else if(button.dataset.action==="confirm-import"){
      button.disabled=true;
      await api("/api/downloads/import",downloadState.pending);
      $('#modal').close();
      await updateState();
      toast(tr("Criando backups e preparando os arquivos aprovados…"));
    } else if(button.dataset.action==="cancel-downloads"){
      await api("/api/downloads/cancel",{});
      toast(tr("Cancelamento solicitado. Os arquivos originais continuam preservados."));
    }
  } catch(error){button.disabled=false;toast(error.message);}
});
