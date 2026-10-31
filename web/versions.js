"use strict";

const versionFilters = [["all","All"],["studio","Studio"],["live","Live"],["acoustic","Acoustic"],["demo-remix","Demo / Remix"],["compilation","Compilation"]];
const recordingLabels = {studio:"Studio",live:"Live",acoustic:"Acoustic",demo:"Demo",remix:"Remix",instrumental:"Instrumental",remaster:"Remaster",compilation:"Compilation",special_edition:"Special edition"};

function versionMatches(version, choice) {
  const marks=version?.marks||[];
  if(choice==="all")return true;
  if(choice==="demo-remix")return marks.includes("demo")||marks.includes("remix");
  if(choice==="studio")return version?.type==="studio"&&!marks.includes("compilation");
  return marks.includes(choice);
}

function versionChips(selected, attribute) {
  return `<div class="version-filters" role="group" aria-label="${tr("Recording type")}">${versionFilters.map(([key,label])=>`<button type="button" class="version-filter" ${attribute}="${key}" aria-pressed="${key===selected}">${tr(label)}</button>`).join("")}</div>`;
}

function versionBadges(version) {
  const marks=(version?.marks||[]).filter(mark=>Object.hasOwn(recordingLabels,mark));
  if(!marks.length)return `<span class="version-badge unknown">${tr("Unclassified")}</span>`;
  return `<span class="version-badges">${marks.map(mark=>`<span class="version-badge ${mark==="live"?"live":""}">${escapeHTML(tr(recordingLabels[mark]))}</span>`).join("")}</span>`;
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
