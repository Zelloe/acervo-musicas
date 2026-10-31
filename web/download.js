"use strict";

const simpleDownloadState = {mode:"artista",query:"",folder:"",bitrate:"320k",catalogId:null,seenCatalog:null,seenJob:null,selected:new Set(),parents:[],versionFilter:"todas"};
const simpleModeLabels = {artista:["Artista","Ex.: Faith No More"],musica:["Música ou artista","Ex.: Pink Floyd Time"],link:["Link do Spotify","Link de música, álbum ou playlist"]};

async function searchFromGlobal(mode, query) {
  if(state.summary?.job?.estado==="executando")throw new Error(tr("Aguarde a operação atual terminar para iniciar outra busca."));
  simpleFormValues($('#simple-download-form'));
  if(!simpleDownloadState.folder)simpleDownloadState.folder=(await api("/api/baixar")).pasta;
  await api("/api/baixar/buscar",{tipo:mode,consulta:query,pasta:simpleDownloadState.folder});
  simpleDownloadState.mode=mode;
  simpleDownloadState.query=query;
  simpleDownloadState.parents=[];
  state.search="";state.searchArtist="";state.searchAlbum="";state.searchLabel="";
  await updateState();
  await navigate("downloads");
}

function simpleFormValues(form) {
  if(!form)return;
  simpleDownloadState.query=form.elements.consulta.value;
  simpleDownloadState.folder=form.elements.pasta.value;
  simpleDownloadState.bitrate=form.elements.bitrate.value;
}

function simpleVersionAttributes(row) {
  return `data-version-row data-version-type="${escapeHTML(row.versao?.tipo||"")}" data-version-marks="${escapeHTML((row.versao?.marcas||[]).join(" "))}"`;
}

function simpleVisibleSelection() {
  return [...document.querySelectorAll('.simple-track-check:checked')].filter(input=>!input.closest('[data-version-row]').hidden).map(input=>input.value);
}

function updateSimpleSelection() {
  const visible=simpleVisibleSelection().length;
  const hidden=simpleDownloadState.selected.size-visible;
  const label=$('#simple-selection-count');
  if(label)label.textContent=`${number(visible)} ${tr("selecionadas neste filtro")}${hidden?` · ${number(hidden)} ${tr("marcadas em outros filtros")}`:""}`;
  const count=$('#simple-download-count');
  if(count)count.textContent=number(visible);
}

function applySimpleVersionFilter() {
  const catalog=$('#simple-catalog');
  if(!catalog)return;
  const rows=[...catalog.querySelectorAll('[data-version-row]')];
  for(const row of rows)row.hidden=!recordingMatches({tipo:row.dataset.versionType,marcas:row.dataset.versionMarks.split(" ")},simpleDownloadState.versionFilter);
  catalog.querySelectorAll('[data-simple-version]').forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.simpleVersion===simpleDownloadState.versionFilter)));
  const visible=rows.filter(row=>!row.hidden).length;
  const count=$('#simple-visible-count');
  if(count)count.textContent=`${number(visible)} ${tr("de")} ${number(rows.length)} ${tr("resultados")}`;
  const empty=$('#simple-empty-filter');
  if(empty)empty.hidden=visible>0;
  updateSimpleSelection();
}

function captureSimpleDownloadJob(job) {
  if(!job || !["busca_direta","download_direto"].includes(job.tipo) || job.estado==="executando")return false;
  const key=`${job.id}:${job.estado}`;
  if(simpleDownloadState.seenJob===key)return false;
  simpleDownloadState.seenJob=key;
  if(job.resultado?.catalogo){
    simpleDownloadState.catalogId=job.resultado.catalogo;
    simpleDownloadState.seenCatalog=null;
  }
  return true;
}

function simpleDownloadResults(rows) {
  if(!rows?.length)return "";
  const counts={baixado:0,ja_existia:0,erro:0,nao_baixado:0};
  for(const row of rows)counts[row.estado]=(counts[row.estado]||0)+1;
  return `<section class="download-panel"><div class="section-heading"><div><h2>${tr("Resultado dos downloads")}</h2><p>${number(counts.baixado)} ${tr("baixadas ·")} ${number(counts.ja_existia)} ${tr("já existiam ·")} ${number(counts.erro)} ${tr("com erro")}${counts.nao_baixado?` · ${number(counts.nao_baixado)} ${tr("não baixadas")}`:""}</p></div></div><ul class="simple-result-list">${rows.map(row=>`<li><strong>${escapeHTML(row.artista)} — ${escapeHTML(row.titulo)}</strong><span class="${["erro","nao_baixado"].includes(row.estado)?"download-warning":"source"}">${escapeHTML(row.estado==="baixado"?"Salva em "+row.arquivo:row.estado==="nao_baixado"?tr("Não baixada.")+" "+(row.mensagem||tr("Lote interrompido.")):row.mensagem||tr("Já existe na pasta"))}</span>${row.genero_preenchido?`<span class="source">${tr("Gênero:")} ${escapeHTML(row.genero_preenchido)}</span>`:""}</li>`).join("")}</ul></section>`;
}

async function renderSimpleDownloads() {
  captureSimpleDownloadJob(state.summary.job);
  const query=simpleDownloadState.catalogId?`?catalogo=${encodeURIComponent(simpleDownloadState.catalogId)}`:"";
  const info=await api("/api/baixar"+query);
  if(!simpleDownloadState.folder)simpleDownloadState.folder=info.pasta;
  const catalog=info.catalogo;
  const busy=state.summary.job?.estado==="executando";
  const mode=simpleDownloadState.mode;
  const [label,placeholder]=simpleModeLabels[mode].map(tr);
  let results="";
  if(catalog){
    simpleDownloadState.catalogId=catalog.id;
    if(simpleDownloadState.seenCatalog!==catalog.id){
      simpleDownloadState.seenCatalog=catalog.id;
      simpleDownloadState.selected=new Set(catalog.itens.filter(row=>catalog.tipo==="faixas"&&!row.ja_tenho).map(row=>row.id));
    }
    const headingText={albuns:tr("Escolha um álbum"),edicoes:tr("Escolha a edição"),faixas:tr("Marque as faixas")}[catalog.tipo];
    const back=simpleDownloadState.parents.length?`<button class="button small ghost" data-action="simple-back">${tr("Voltar")}</button>`:"";
    const body=catalog.tipo==="faixas"?`
      <div class="simple-selection-tools"><span id="simple-selection-count"></span><div class="download-actions"><button class="text-button" data-action="simple-select-new">${tr("Selecionar novas visíveis")}</button><button class="text-button" data-action="simple-select-all">${tr("Selecionar visíveis")}</button><button class="text-button" data-action="simple-clear">${tr("Limpar seleção")}</button></div></div>
      <p class="download-hint">${tr("Correspondências por artista e título na pasta consultada:")} <span class="simple-path">${escapeHTML(catalog.pasta)}</span></p>
      <div class="simple-track-list">${catalog.itens.map(row=>`<label class="simple-track version-filtered" ${simpleVersionAttributes(row)} data-existing="${!!row.ja_tenho}"><input class="check simple-track-check" type="checkbox" value="${row.id}" ${simpleDownloadState.selected.has(row.id)?"checked":""} ${busy?"disabled":""}>${catalogPhoto(row.capa)}<span class="simple-track-copy"><strong>${escapeHTML(row.titulo)}</strong><small>${escapeHTML(row.artista)} · ${escapeHTML(row.album)}${row.ano?` · ${escapeHTML(row.ano)}`:""}</small>${recordingBadges(row.versao)}</span><span class="simple-track-time">${duration(row.duracao)}</span>${row.ja_tenho?`<span class="chip neutral">${tr("Nome encontrado na pasta")}</span>`:""}</label>`).join("")}</div>
      <div class="download-actions simple-save-row"><span class="simple-path">${tr("Salvar diretamente em")} <strong id="simple-save-folder">${escapeHTML(simpleDownloadState.folder)}</strong></span><button class="button primary" data-action="simple-download" ${busy?"disabled":""}>${icon("download")} ${tr("Baixar selecionadas (")}<span id="simple-download-count">0</span>)</button></div>`:
      `<div class="simple-choice-list">${catalog.itens.map(row=>`<button class="simple-choice version-filtered" ${simpleVersionAttributes(row)} data-simple-open="${row.id}" ${busy?"disabled":""}>${catalogPhoto(row.capa)}<span><strong>${escapeHTML(row.titulo)}</strong><small>${escapeHTML(row.ano||"")}${row.faixas_total?` · ${number(row.faixas_total)} ${tr("faixas")}`:""}</small>${recordingBadges(row.versao)}</span>${icon("arrow")}</button>`).join("")}</div>`;
    results=`<section class="download-panel" id="simple-catalog"><div class="section-heading"><div><h2>${headingText}</h2><p>${escapeHTML(catalog.titulo)}</p></div>${back}</div>${recordingChips(simpleDownloadState.versionFilter,"data-simple-version")}<p id="simple-visible-count" class="download-hint"></p><div id="simple-empty-filter" hidden>${empty(tr("Nenhuma versão neste filtro"),tr("Escolha Todos ou outro tipo de gravação."),"search")}</div>${catalog.itens.length?body:empty(tr("Nenhum resultado"),tr("Tente outra busca ou cole um link do Spotify."),"search")}<p class="download-hint">${tr("Tipo estimado pelos nomes da faixa e do álbum; confira a edição quando não houver indicação.")}</p></section>`;
  }
  return `${heading(tr("ESCOLHA O QUE QUER OUVIR"),tr("Baixar músicas"),tr("Encontre o álbum, escolha a edição e baixe só as faixas que quiser."))}<form id="simple-download-form" class="download-panel"><div class="tabs simple-search-tabs">${[["artista",tr("Por artista")],["musica",tr("Por música")],["link",tr("Por link")]].map(([key,text])=>`<button type="button" class="tab ${mode===key?"active":""}" data-simple-mode="${key}" aria-pressed="${mode===key}" ${busy?"disabled":""}>${text}</button>`).join("")}</div><div class="simple-search-fields"><label><span id="simple-query-label">${label}</span><input class="field" name="consulta" value="${escapeHTML(simpleDownloadState.query)}" placeholder="${escapeHTML(placeholder)}" required maxlength="1200"></label><button class="button primary" type="submit" ${busy?"disabled":""}>${icon("search")} ${mode==="link"?tr("Ver faixas"):tr("Buscar")}</button></div><div class="simple-settings"><label>${tr("Pasta para salvar")}<input class="field" name="pasta" value="${escapeHTML(simpleDownloadState.folder)}" placeholder="${tr("Caminho de uma pasta existente")}" required></label><label>MP3<select class="field" name="bitrate">${["320k","256k","192k","128k"].map(v=>`<option value="${v}" ${simpleDownloadState.bitrate===v?"selected":""}>${v.slice(0,-1)} kbps</option>`).join("")}</select></label></div><p class="download-hint">${tr("Os arquivos vão direto para a pasta escolhida. Arquivos existentes são preservados. A taxa do MP3 não melhora a qualidade da fonte.")}</p></form>${results}${simpleDownloadResults(info.resultado)}`;
}

document.addEventListener("input",event=>{
  if(!event.target.closest("#simple-download-form"))return;
  simpleFormValues(event.target.closest("form"));
  const folder=$('#simple-save-folder');
  if(folder)folder.textContent=simpleDownloadState.folder;
});

document.addEventListener("change",event=>{
  if(event.target.matches(".simple-track-check")){
    if(event.target.checked)simpleDownloadState.selected.add(event.target.value);
    else simpleDownloadState.selected.delete(event.target.value);
    updateSimpleSelection();
  }
  if(event.target.closest("#simple-download-form"))simpleFormValues(event.target.closest("form"));
});

document.addEventListener("submit",async event=>{
  if(event.target.id!=="simple-download-form")return;
  event.preventDefault();
  const submit=event.target.querySelector('[type="submit"]');
  submit.disabled=true;
  simpleFormValues(event.target);
  try {
    await api("/api/baixar/buscar",{tipo:simpleDownloadState.mode,consulta:simpleDownloadState.query,pasta:simpleDownloadState.folder});
    simpleDownloadState.parents=[];
    await updateState();
    await render();
  }catch(error){submit.disabled=false;toast(error.message);}
});

document.addEventListener("click",async event=>{
  const button=event.target.closest("button");
  if(!button||button.disabled)return;
  try {
    if(button.dataset.simpleVersion){
      simpleDownloadState.versionFilter=button.dataset.simpleVersion;
      applySimpleVersionFilter();
    } else if(button.dataset.simpleMode){
      const form=$('#simple-download-form');
      simpleFormValues(form);
      if(simpleDownloadState.mode!==button.dataset.simpleMode)simpleDownloadState.query="";
      simpleDownloadState.mode=button.dataset.simpleMode;
      const [label,placeholder]=simpleModeLabels[simpleDownloadState.mode].map(tr);
      $('#simple-query-label').textContent=label;
      form.elements.consulta.value=simpleDownloadState.query;
      form.elements.consulta.placeholder=placeholder;
      form.querySelector('[type="submit"]').innerHTML=icon("search")+" "+(simpleDownloadState.mode==="link"?tr("Ver faixas"):tr("Buscar"));
      document.querySelectorAll('[data-simple-mode]').forEach(el=>{el.classList.toggle("active",el===button);el.setAttribute("aria-pressed",String(el===button));});
      form.elements.consulta.focus();
    } else if(button.dataset.simpleOpen){
      button.disabled=true;
      await api("/api/baixar/abrir",{catalogo:simpleDownloadState.catalogId,item:button.dataset.simpleOpen,pasta:simpleDownloadState.folder});
      simpleDownloadState.parents.push(simpleDownloadState.catalogId);
      await updateState();
      await render();
    } else if(button.dataset.action==="simple-back"){
      simpleDownloadState.catalogId=simpleDownloadState.parents.pop()||null;
      simpleDownloadState.seenCatalog=null;
      await render();
    } else if(["simple-select-new","simple-select-all","simple-clear"].includes(button.dataset.action)){
      if(button.dataset.action==="simple-clear")simpleDownloadState.selected.clear();
      for(const input of document.querySelectorAll('.simple-track-check')){
        if(input.closest('.simple-track').hidden&&button.dataset.action!=="simple-clear")continue;
        const existing=input.closest('.simple-track').dataset.existing==="true";
        input.checked=button.dataset.action==="simple-select-all" || (button.dataset.action==="simple-select-new"&&!existing);
        if(input.checked)simpleDownloadState.selected.add(input.value);
        else simpleDownloadState.selected.delete(input.value);
      }
      updateSimpleSelection();
    } else if(button.dataset.action==="simple-download"){
      const selected=simpleVisibleSelection();
      if(!selected.length)throw new Error(tr("Selecione pelo menos uma faixa visível neste filtro para baixar."));
      simpleFormValues($('#simple-download-form'));
      button.disabled=true;
      await api("/api/baixar/baixar",{catalogo:simpleDownloadState.catalogId,ids:selected,pasta:simpleDownloadState.folder,bitrate:simpleDownloadState.bitrate});
      await updateState();
      await render();
      toast(tr("Download iniciado na pasta escolhida."));
    }
  }catch(error){button.disabled=false;toast(error.message);}
});
