"use strict";

const downloadState = {batchId:null, batch:null, fetchIds:new Set(), choices:new Map(), draft:{}, pending:null};
const fieldNames = {titulo:"Título",artista:"Artista",artista_album:"Artista do álbum",album:"Álbum",genero:"Gênero",ano:"Ano",faixa:"Faixa",disco:"Disco"};
const batchLabels = {consultando:"Consultando",pronto:"Catálogo pronto",baixando:"Baixando",revisao:"Pronto para revisão",cancelado:"Cancelado",erro:"Precisa de atenção",importado:"Importado"};
const stagedURL = (kind,batch,row) => `/api/downloads/${kind}?${new URLSearchParams({id:batch,faixa:row})}`;

function downloadForm(info) {
  const draft=downloadState.draft;
  const input=(name,label,placeholder="")=>`<label>${label}<input class="field" name="${name}" value="${escapeHTML(draft[name]||"")}" placeholder="${escapeHTML(placeholder)}"></label>`;
  const select=(name,label,items)=>`<label>${label}<select class="field" name="${name}">${items.map(([v,t])=>`<option value="${escapeHTML(v)}" ${draft[name]===v?"selected":""}>${escapeHTML(t)}</option>`).join("")}</select></label>`;
  const busy=state.summary.job?.estado==="executando";
  return `<form id="download-form" class="download-panel"><div class="section-heading"><div><h2>Mais música no seu acervo.</h2><p>Comece pelo catálogo. Você decide o que entra na coleção.</p></div><span class="chip">1 · Preparar</span></div><div class="download-form-grid"><label class="wide">Link ou busca<input class="field" name="consulta" required maxlength="1200" placeholder="Link do Spotify, YouTube ou artista e álbum" value="${escapeHTML(draft.consulta||"")}"></label>${select("ferramenta","Ferramenta",[["spotdl","spotDL · catálogo Spotify e áudio YouTube"],["yt-dlp","yt-dlp · link direto do YouTube"]])}${select("modo","O que deseja fazer?",[["faltantes","Baixar apenas o que estiver faltando"],["completo","Preparar um álbum completo novamente"],["metadados","Unificar só os metadados existentes"]])}${input("album","Nome unificado do álbum","Manter o nome da referência")}${input("genero","Gênero","Sugerir pela fonte e pelas regras locais")}${input("artista","Artista","Usar os créditos da fonte")}${input("artista_album","Artista do álbum","Usar o artista principal")}${input("ano","Ano / data","AAAA ou AAAA-MM-DD")}${select("bitrate","MP3 · taxa de saída",[["auto","Automática"],["320k","320 kbps"],["256k","256 kbps"],["192k","192 kbps"],["128k","128 kbps"]])}${select("destino","Pasta existente para novas faixas",info.pastas.map(p=>[p,p.replace(/^\/home\/[^/]+/,"~")]))}</div><div class="download-options"><label><input class="check" type="checkbox" name="unificar_album" ${draft.unificar_album!==false?"checked":""}> Sugerir o nome base para edições remasterizadas / deluxe</label><label><input class="check" type="checkbox" name="forcar_genero" ${draft.forcar_genero?"checked":""}> Propor também a troca de gêneros já preenchidos</label></div><p class="download-hint">O spotDL usa o Spotify como catálogo e busca o áudio no YouTube. A taxa de saída não melhora a qualidade da fonte. Novos arquivos serão MP3; um FLAC existente pode receber tags, mas não será substituído por MP3.</p><div class="download-tools">${Object.entries(info.ferramentas).filter(([k])=>k!=="deno").map(([k,v])=>`<span class="chip ${v.disponivel?"":"amber"}">${escapeHTML(k)} · ${v.disponivel?"disponível":"não instalado"}</span>`).join("")}</div><div class="download-actions"><button class="button primary" type="submit" ${busy?"disabled":""}>Consultar e comparar ${icon("arrow")}</button></div></form>`;
}

function selectedLocal(row,choice) {
  return row.correspondencias.find(m=>m.id===choice?.alvo) || (row.correspondencias.length===1?row.correspondencias[0]:null);
}

function diffHTML(changes) {
  return Object.entries(changes||{}).map(([field,c])=>`<div class="download-diff"><span>${escapeHTML(fieldNames[field]||field)}</span><span class="change-old">${escapeHTML(join(c.atual)||"Não definido")}</span><span class="change-new">${icon("arrow")}${escapeHTML(join(c.proposto))}</span></div>`).join("");
}

function downloadRow(batch,row,index) {
  const choice=downloadState.choices.get(row.id)||{};
  const local=selectedLocal(row,choice);
  const available=row.estado==="baixado";
  const busy=state.summary.job?.estado==="executando";
  const imported=!!batch.importacao;
  const canFetch=!imported && batch.modo!=="metadados" && !available && (batch.modo==="completo"||!row.correspondencias.length);
  const metadata=row.metadados;
  const art=available&&row.real.capas.length?`<img src="${stagedURL("cover",batch.id,row.id)}" alt="">`:`<div class="download-art">${icon("disc")}</div>`;
  const options=[['',"Manter a biblioteca como está"]];
  if(row.correspondencias.some(m=>Object.keys(m.alteracoes).length))options.push(["metadados","Aplicar somente as tags sugeridas"]);
  if(available)options.push(["adicionar",row.correspondencias.length?"Adicionar como outra versão":"Adicionar à biblioteca"]);
  if(available && row.correspondencias.some(m=>m.formato==="MP3"))options.push(["substituir","Substituir um MP3 · com backup"]);
  const changes=choice.acao==="metadados"?local?.alteracoes:choice.acao==="substituir"?local?.alteracoes_substituicao:null;
  return `<article class="download-track" data-row="${row.id}"><div class="download-track-head"><div class="download-cover">${art}</div><div class="download-title"><span class="section-kicker">FAIXA ${escapeHTML(join(metadata.faixa)||index+1)} · DISCO ${escapeHTML(join(metadata.disco))}</span><h3>${escapeHTML(join(metadata.titulo))}</h3><p>${escapeHTML(join(metadata.artista))} · ${escapeHTML(join(metadata.album)||"Álbum não informado")}</p></div><span class="chip ${row.estado==="erro"?"amber":""}">${available?"Áudio pronto":row.correspondencias.length?`${row.correspondencias.length} versão local`:row.estado==="erro"?"Falhou":"Nova faixa"}</span></div><div class="download-tags"><span>${icon("tag")}${escapeHTML(join(metadata.genero)||"Gênero pendente")}</span><span>${escapeHTML(join(metadata.ano)||"Ano pendente")}</span><span>${duration(row.fonte.duration)}</span></div><p class="source">Gênero: ${escapeHTML(row.origem_genero)}</p>${row.correspondencias.map(m=>`<div class="local-match"><span class="format ${m.formato==="FLAC"?"flac":""}">${m.formato}</span><span>${escapeHTML(m.arquivo.split("/").pop())}</span><span>${duration(m.duracao_segundos)}</span></div>`).join("")}${[...new Set(row.avisos)].map(v=>`<p class="download-warning">${escapeHTML(v)}</p>`).join("")}${row.erro?`<p class="download-warning">${escapeHTML(row.erro)}</p>`:""}<div class="download-row-actions">${canFetch?`<label><input class="check download-check" type="checkbox" data-id="${row.id}" ${downloadState.fetchIds.has(row.id)?"checked":""} ${busy?"disabled":""}> Baixar para revisão</label>`:""}${available?`<button class="button small ghost" data-stage-play="${row.id}">${icon("play")} Ouvir download</button>`:""}${row.estado==="erro"?`<button class="text-button" data-download-log="${row.id}">Ver registro</button>`:""}</div>${!imported?`<label class="import-choice">Ao aprovar<select class="field import-action" data-id="${row.id}" ${busy?"disabled":""}>${options.map(([value,label])=>`<option value="${value}" ${choice.acao===value?"selected":""}>${label}</option>`).join("")}</select></label>${choice.acao&&choice.acao!=="adicionar"&&row.correspondencias.length>1?`<label class="import-choice">Arquivo local a alterar<select class="field import-target" data-id="${row.id}"><option value="">Escolha a versão…</option>${row.correspondencias.map(m=>`<option value="${m.id}" ${choice.alvo===m.id?"selected":""}>${escapeHTML(m.formato+" · "+m.arquivo.split("/").pop())}</option>`).join("")}</select></label>`:""}${changes?`<div class="download-diffs">${diffHTML(changes)}</div>`:""}${choice.acao==="adicionar"?`<p class="source">Novo arquivo: ${escapeHTML(row.arquivo_novo.split("/").pop())}</p>`:""}`:""}</article>`;
}

async function openDownloadBatch(id) {
  downloadState.batchId=id;
  downloadState.batch=await api(`/api/downloads/batch?id=${encodeURIComponent(id)}`);
  downloadState.choices.clear();
  downloadState.fetchIds=new Set(downloadState.batch.faixas.filter(r=>!r.correspondencias.length&&r.estado!=="baixado"&&downloadState.batch.modo!=="metadados").map(r=>r.id));
  await render();
}

async function renderDownloads() {
  const info=await api("/api/downloads");
  const list=`<section class="download-panel"><div class="section-heading"><div><h2>Seus lotes de revisão</h2><p>Downloads permanecem fora da coleção até a aprovação.</p></div></div>${info.lotes.length?`<div class="batch-list">${info.lotes.map(b=>`<button class="batch-item ${downloadState.batchId===b.id?"active":""}" data-batch="${b.id}"><span>${icon("disc")}</span><span><strong>${escapeHTML(b.consulta)}</strong><small>${escapeHTML(new Date(b.criado_em).toLocaleString("pt-BR"))} · ${escapeHTML(b.ferramenta)}</small></span><span class="chip">${escapeHTML(batchLabels[b.estado]||b.estado)}</span>${icon("arrow")}</button>`).join("")}</div>`:'<p class="download-hint">Seu primeiro lote aparecerá aqui.</p>'}</section>`;
  let detail="";
  if(downloadState.batchId){
    const b=await api(`/api/downloads/batch?id=${downloadState.batchId}`);
    downloadState.batch=b;
    const busy=state.summary.job?.estado==="executando";
    const cancel=busy&&["download","catalogo"].includes(state.summary.job.tipo);
    detail=`<section class="section"><div class="section-heading"><div><span class="section-kicker">2 · OUVIR E REVISAR</span><h2>${escapeHTML(join(b.faixas[0]?.metadados.album)||"Prévia do catálogo")}</h2><p>${b.faixas.length} faixas · ${b.faixas.filter(r=>r.correspondencias.length).length} com correspondência local · ${b.faixas.filter(r=>r.estado==="baixado").length} downloads prontos</p></div><button class="text-button" data-action="catalog-log">Registro da consulta</button></div>${b.erro?`<div class="error-box">${escapeHTML(b.erro)}</div>`:""}${b.avisos.map(v=>`<div class="error-box">${escapeHTML(v)}</div>`).join("")}${!b.importacao?`<div class="download-panel download-batch-actions"><p>Confira os créditos, o gênero e a versão. As correspondências usam tags e duração; ainda não há confirmação por impressão digital.</p><div class="download-actions">${b.modo!=="metadados"?`<button class="button ghost" data-action="select-downloads" ${busy?"disabled":""}>Selecionar para download</button><button class="button primary" data-action="fetch-downloads" ${busy?"disabled":""}>${icon("download")} Baixar seleção</button>`:""}<button class="button secondary" data-action="review-import" ${busy?"disabled":""}>Revisar aprovação ${icon("arrow")}</button>${cancel?'<button class="button ghost" data-action="cancel-downloads">Cancelar download</button>':""}</div></div>`:'<div class="review-summary">'+icon("check")+'<p><strong>Lote importado.</strong>O backup e a opção de desfazer estão no Histórico. Para outras alterações, prepare uma nova prévia.</p></div>'}<div class="download-track-list">${b.faixas.map((r,i)=>downloadRow(b,r,i)).join("")}</div></section>`;
  }
  return `${heading("DESCUBRA, COMPLETE, UNIFIQUE","Downloads e metadados","Álbuns inteiros, faixas novas e gêneros nos seus arquivos.")}${downloadForm(info)}${list}${detail}`;
}

async function previewDownloadImport() {
  const selections=[...downloadState.choices].filter(([,v])=>v.acao).map(([id,v])=>({id,...v}));
  if(!selections.length)throw new Error("Escolha a ação das faixas que deseja aprovar.");
  const result=await api("/api/downloads/preview",{id:downloadState.batchId,selecoes:selections});
  downloadState.pending={id:downloadState.batchId,selecoes:selections};
  let backupSize=0;
  const items=result.operacoes.map(op=>{
    const row=downloadState.batch.faixas.find(r=>r.id===op.id);
    const local=row.correspondencias.find(m=>m.arquivo===op.arquivo);
    if(local)backupSize+=local.tamanho_bytes;
    const labels={adicionar:"Adicionar novo arquivo",metadados:"Atualizar somente metadados",substituir:"Substituir o áudio do MP3"};
    return `<li><strong>${escapeHTML(join(row.metadados.artista))} — ${escapeHTML(join(row.metadados.titulo))}</strong><span>${labels[op.acao]} · ${escapeHTML(op.arquivo.split("/").pop())}</span>${op.acao==="adicionar"?diffHTML(Object.fromEntries(Object.keys(fieldNames).map(k=>[k,{atual:[],proposto:row.real[k]}]))):diffHTML(op.alteracoes)}</li>`;
  }).join("");
  modal(`${modalHead("Aprovar a entrada na coleção?",`${result.operacoes.length} operações selecionadas. Só esta confirmação modifica sua biblioteca.`)}<div class="modal-body"><div class="backup-note">${icon("shield")}<span>Backup completo dos arquivos existentes: <strong>${bytes(backupSize)}</strong>. As adições também podem ser desfeitas: o arquivo será guardado fora da biblioteca. Confira as mudanças abaixo.</span></div><ul class="confirm-list">${items}</ul></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">Voltar à revisão</button><button class="button primary" data-action="confirm-import">${icon("check")} Criar backup e aprovar</button></div>`);
}

async function playStaged(id) {
  const row=downloadState.batch.faixas.find(r=>r.id===id);
  if(!row?.real)throw new Error("Áudio ainda não disponível.");
  state.current={...row.real,id:`stage-${id}`};
  state.queue=[];
  $('#player-title').textContent=join(row.real.titulo);
  $('#player-artist').textContent=join(row.real.artista)+" · prévia do download";
  $('#player-cover').innerHTML=row.real.capas.length?`<img src="${stagedURL("cover",downloadState.batchId,id)}" alt="">`:icon("disc");
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
    toast("Consultando o catálogo e comparando com a biblioteca…");
  } catch(error){submit.disabled=false;toast(error.message);}
});

function downloadDraft(form) {
  const data=Object.fromEntries(new FormData(form));
  for(const name of ["unificar_album","forcar_genero"])data[name]=form.elements[name].checked;
  downloadState.draft=data;
  return data;
}

document.addEventListener("input",event=>{if(event.target.closest("#download-form"))downloadDraft(event.target.closest("form"));});
document.addEventListener("change",event=>{
  const el=event.target;
  if(el.matches(".download-check")){if(el.checked)downloadState.fetchIds.add(el.dataset.id);else downloadState.fetchIds.delete(el.dataset.id);}
  if(el.matches(".import-action,.import-target")){
    const choice=downloadState.choices.get(el.dataset.id)||{};
    choice[el.matches(".import-action")?"acao":"alvo"]=el.value;
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
      modal(`${modalHead("Registro da ferramenta","Detalhes da consulta ou download.")}<div class="modal-body"><pre class="download-log">${escapeHTML(data.registro)}</pre></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">Fechar</button></div>`);
    } else if(button.dataset.action==="select-downloads"){
      downloadState.fetchIds=new Set(downloadState.batch.faixas.filter(r=>r.estado!=="baixado"&&(downloadState.batch.modo==="completo"||!r.correspondencias.length)).map(r=>r.id));
      document.querySelectorAll(".download-check").forEach(el=>{el.checked=downloadState.fetchIds.has(el.dataset.id);});
    } else if(button.dataset.action==="fetch-downloads"){
      if(!downloadState.fetchIds.size)throw new Error("Selecione ao menos uma faixa para baixar.");
      button.disabled=true;
      await api("/api/downloads/fetch",{id:downloadState.batchId,faixas:[...downloadState.fetchIds]});
      await updateState();
      await render();
      toast("Download iniciado fora da biblioteca. Você poderá ouvir antes de aprovar.");
    } else if(button.dataset.action==="review-import")await previewDownloadImport();
    else if(button.dataset.action==="confirm-import"){
      button.disabled=true;
      await api("/api/downloads/import",downloadState.pending);
      $('#modal').close();
      await updateState();
      toast("Criando backups e preparando os arquivos aprovados…");
    } else if(button.dataset.action==="cancel-downloads"){
      await api("/api/downloads/cancel",{});
      toast("Cancelamento solicitado. Os arquivos originais continuam preservados.");
    }
  } catch(error){button.disabled=false;toast(error.message);}
});
