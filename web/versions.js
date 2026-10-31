"use strict";

const versionFilters = [["all","Todos"],["studio","Estúdio"],["live","Ao vivo"],["acoustic","Acústico"],["demo-remix","Demo / Remix"],["compilation","Coletânea"]];

function versionMatches(version, choice) {
  const marks=version?.marks||[];
  if(choice==="all")return true;
  if(choice==="demo-remix")return marks.includes("demo")||marks.includes("remix");
  if(choice==="studio")return version?.type==="studio"&&!marks.includes("compilation");
  return marks.includes(choice);
}

function versionChips(selected, attribute) {
  return `<div class="version-filters" role="group" aria-label="${tr("Tipo de gravação")}">${versionFilters.map(([key,label])=>`<button type="button" class="version-filter" ${attribute}="${key}" aria-pressed="${key===selected}">${tr(label)}</button>`).join("")}</div>`;
}

function versionBadges(version) {
  if(!version?.labels?.length)return `<span class="version-badge unknown">${tr("Sem classificação")}</span>`;
  return `<span class="version-badges">${version.labels.map((label,index)=>`<span class="version-badge ${version.marks?.[index]==="live"?"live":""}">${escapeHTML(tr(label))}</span>`).join("")}</span>`;
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
