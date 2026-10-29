"use strict";

const versionFilters = [["todas","Todos"],["estudio","Estúdio"],["ao_vivo","Ao vivo"],["acustico","Acústico"],["demo-remix","Demo / Remix"],["coletanea","Coletânea"]];

function versionMatches(version, choice) {
  const marks=version?.marcas||[];
  if(choice==="todas")return true;
  if(choice==="demo-remix")return marks.includes("demo")||marks.includes("remix");
  if(choice==="estudio")return version?.tipo==="estudio"&&!marks.includes("coletanea");
  return marks.includes(choice);
}

function versionChips(selected, attribute) {
  return `<div class="version-filters" role="group" aria-label="Tipo de gravação">${versionFilters.map(([key,label])=>`<button type="button" class="version-filter" ${attribute}="${key}" aria-pressed="${key===selected}">${label}</button>`).join("")}</div>`;
}

function versionBadges(version) {
  if(!version?.rotulos?.length)return '<span class="version-badge unknown">Sem classificação</span>';
  return `<span class="version-badges">${version.rotulos.map(label=>`<span class="version-badge ${version.marcas?.includes("ao_vivo")&&label==="Ao vivo"?"live":""}">${escapeHTML(label)}</span>`).join("")}</span>`;
}

function safePhoto(url) {
  return typeof url==="string"&&/^https:\/\/(?:[a-z0-9-]+\.googleusercontent\.com|i\.scdn\.co|mosaic\.scdn\.co|image-cdn-[a-z0-9-]+\.spotifycdn\.com)\/[^\s"'<>]+$/.test(url)?url:"";
}

function remotePhoto(url, artist=false) {
  const source=safePhoto(url);
  return `<span class="remote-photo${artist?" artist-photo":""}">${icon(artist?"music":"disc")}${source?`<img src="${escapeHTML(source)}" alt="" referrerpolicy="no-referrer" loading="lazy" decoding="async">`:""}</span>`;
}

document.addEventListener("error",event=>{
  if(event.target.matches?.('.remote-photo img'))event.target.classList.add("photo-failed");
},true);
