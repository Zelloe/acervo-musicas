"use strict";

const simpleDownloadState = {
  mode: "artist",
  query: "",
  folder: "",
  bitrate: "320k",
  catalogId: null,
  seenCatalog: null,
  seenJob: null,
  selected: new Set(),
  parents: [],
  versionFilter: "all",
  loading: false,
  serial: 0,
  enqueuing: false,
  error: "",
};
const simpleModeLabels = {
  artist: ["Artist", "E.g. Faith No More"],
  song: ["Track or artist", "E.g. Pink Floyd Time"],
  link: ["Spotify link", "Track, album or playlist link"],
};
const catalogStore = new Map();
const catalogSelections = new Map();

function rememberCatalog(catalog) {
  catalogStore.set(catalog.id, catalog);
  while (catalogStore.size > 40) {
    const oldest = catalogStore.keys().next().value;
    catalogStore.delete(oldest);
    catalogSelections.delete(oldest);
  }
  return catalog;
}

async function storedCatalog(id) {
  if (catalogStore.has(id)) return catalogStore.get(id);
  const info = await api(`/api/download?${new URLSearchParams({ catalog: id })}`);
  return rememberCatalog(info.catalog);
}

function useCatalog(catalog) {
  rememberCatalog(catalog);
  simpleDownloadState.catalogId = catalog.id;
  if (simpleDownloadState.seenCatalog !== catalog.id) {
    if (simpleDownloadState.seenCatalog)
      catalogSelections.set(simpleDownloadState.seenCatalog, {
        selected: new Set(simpleDownloadState.selected),
        filter: simpleDownloadState.versionFilter,
      });
    const saved = catalogSelections.get(catalog.id);
    simpleDownloadState.seenCatalog = catalog.id;
    simpleDownloadState.selected = saved
      ? new Set(saved.selected)
      : new Set(catalog.items.filter((row) => catalog.type === "tracks" && !row.already_owned).map((row) => row.id));
    simpleDownloadState.versionFilter = saved?.filter || "all";
  }
}

async function browseCatalog(path, data) {
  if (!simpleDownloadState.folder) simpleDownloadState.folder = (await api("/api/download")).folder;
  const { catalog } = await api(path, { ...data, folder: simpleDownloadState.folder });
  return rememberCatalog(catalog);
}

async function searchFromGlobal(mode, query) {
  if (mode === "artist") return openPage("artist", { name: query, scope: "global" });
  return openPage("search", { query, scope: "global" });
}

function simpleFormValues(form) {
  if (!form) return;
  simpleDownloadState.query = form.elements.query.value;
  simpleDownloadState.folder = form.elements.folder.value;
  simpleDownloadState.bitrate = form.elements.bitrate.value;
}

function simpleVersionAttributes(row) {
  return `data-version-row data-version-type="${escapeHTML(row.version?.type || "")}" data-version-marks="${escapeHTML((row.version?.marks || []).join(" "))}"`;
}

function simpleVisibleSelection() {
  return [...document.querySelectorAll(".simple-track-check:checked")]
    .filter((input) => !input.closest("[data-version-row]").hidden)
    .map((input) => input.value);
}

function updateSimpleSelection() {
  const visible = simpleVisibleSelection().length,
    hidden = simpleDownloadState.selected.size - visible;
  const label = $("#simple-selection-count");
  if (label)
    label.textContent = `${number(visible)} ${tr("selected in this filter")}${hidden ? ` · ${number(hidden)} ${tr("selected in other filters")}` : ""}`;
  const count = $("#simple-download-count");
  if (count) count.textContent = number(visible);
}

function applySimpleVersionFilter() {
  const catalog = $("#simple-catalog");
  if (!catalog) return;
  const rows = [...catalog.querySelectorAll("[data-version-row]")];
  for (const row of rows)
    row.hidden = !recordingMatches(
      { type: row.dataset.versionType, marks: row.dataset.versionMarks.split(" ") },
      simpleDownloadState.versionFilter,
    );
  catalog
    .querySelectorAll("[data-simple-version]")
    .forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.simpleVersion === simpleDownloadState.versionFilter)),
    );
  const visible = rows.filter((row) => !row.hidden).length;
  if ($("#simple-visible-count"))
    $("#simple-visible-count").textContent = `${number(visible)} ${tr("of")} ${number(rows.length)} ${tr("results")}`;
  if ($("#simple-empty-filter")) $("#simple-empty-filter").hidden = visible > 0;
  updateSimpleSelection();
}

function captureSimpleDownloadJob(job) {
  if (!job || job.type !== "direct_search" || job.status !== "completed") return false;
  const key = `${job.id}:${job.status}`;
  if (simpleDownloadState.seenJob === key) return false;
  simpleDownloadState.seenJob = key;
  if (job.result?.catalog) {
    simpleDownloadState.catalogId = job.result.catalog;
    simpleDownloadState.seenCatalog = null;
  }
  return true;
}

function catalogChoice(catalog, row, page) {
  return `<button class="simple-choice version-filtered" ${simpleVersionAttributes(row)} ${page ? `data-page-catalog="${escapeHTML(catalog.id)}" data-page-item="${escapeHTML(row.id)}"` : `data-simple-open="${escapeHTML(row.id)}"`}>${catalogPhoto(row.cover)}<span><strong data-i18n-user="true">${escapeHTML(row.title)}</strong><small>${escapeHTML(row.year || "")}${row.track_count ? ` · ${number(row.track_count)} ${tr("tracks")}` : ""}</small>${recordingBadges(row.version)}</span>${icon("arrow")}</button>`;
}

function catalogChoices(catalog, page = false) {
  return `<div class="simple-choice-list">${catalog.items.map((row) => catalogChoice(catalog, row, page)).join("")}</div>`;
}

function catalogTrack(row) {
  return `<label class="simple-track version-filtered" ${simpleVersionAttributes(row)} data-existing="${!!row.already_owned}"><input class="check simple-track-check" type="checkbox" value="${escapeHTML(row.id)}" ${simpleDownloadState.selected.has(row.id) ? "checked" : ""}>${catalogPhoto(row.cover)}<span class="simple-track-copy"><strong data-i18n-user="true">${escapeHTML(row.title)}</strong><small>${artistLinks([row.artist], "global")} · ${globalAlbumLink(row.album, row.artist)}${row.year ? ` · ${escapeHTML(row.year)}` : ""}</small>${recordingBadges(row.version)}</span><span class="simple-track-time">${duration(row.duration)}</span>${row.already_owned ? `<span class="chip neutral">${tr("Name found in folder")}</span>` : ""}</label>`;
}

function catalogTracks(catalog) {
  return `
    <div class="simple-selection-tools"><span id="simple-selection-count"></span><div class="download-actions"><button class="text-button" data-action="simple-select-new">${tr("Select new visible tracks")}</button><button class="text-button" data-action="simple-select-all">${tr("Select visible tracks")}</button><button class="text-button" data-action="simple-clear">${tr("Clear selection")}</button></div></div>
    <p class="download-hint">${tr("Artist and title matches in the selected folder:")} <span class="simple-path">${escapeHTML(catalog.folder)}</span></p>
    <div class="simple-track-list">${catalog.items.map(catalogTrack).join("")}</div>
    <div class="download-actions simple-save-row"><span class="simple-path">${tr("Save directly to")} <strong id="simple-save-folder">${escapeHTML(simpleDownloadState.folder)}</strong></span><button class="button primary" data-action="simple-download">${icon("download")}<span>${tr("Add to queue (")}<span id="simple-download-count">0</span>)</span></button></div>`;
}

function catalogContent(catalog) {
  const headingText = { albums: tr("Choose an album"), editions: tr("Choose an edition"), tracks: tr("Select tracks") }[
    catalog.type
  ];
  const body = catalog.type !== "tracks" ? catalogChoices(catalog, state.view === "album") : catalogTracks(catalog);
  return `<section class="download-panel" id="simple-catalog"><div class="section-heading"><div><h2>${headingText}</h2><p data-i18n-user="true">${escapeHTML(catalog.title)}</p></div>${state.view === "downloads" && simpleDownloadState.parents.length ? `<button class="button small ghost" data-action="simple-back">${tr("Back")}</button>` : ""}</div>${recordingChips(simpleDownloadState.versionFilter, "data-simple-version")}<p id="simple-visible-count" class="download-hint"></p><div id="simple-empty-filter" hidden>${empty(tr("No versions in this filter"), tr("Choose All or another recording type."), "search")}</div>${catalog.items.length ? body : empty(tr("No results"), tr("Try another search or paste a Spotify link."), "search")}<p class="download-hint">${tr("Type estimated from track and album names and, for albums, checked on MusicBrainz when possible; check the edition when no type is specified.")}</p></section>`;
}

function downloadQueueItem(row, labels) {
  return `<li class="download-queue-item"><div><strong data-i18n-user="true">${escapeHTML(row.title || row.artist)}</strong><span data-i18n-user="true">${escapeHTML(row.artist || "")}</span><small>${number(row.total)} ${tr("tracks")} · ${escapeHTML(labels[row.status] || row.status)}${row.status === "completed" ? ` · ${number(row.downloaded)} ${tr("downloaded ·")} ${number(row.errors)} ${tr("failed")}` : ""}</small>${row.message ? `<p>${escapeHTML(row.message)}</p>` : ""}${row.warnings?.length ? `<p class="download-warning">${row.warnings.map(escapeHTML).join(" ")}</p>` : ""}</div>${row.status === "waiting" ? `<button class="text-button" data-remove-download="${escapeHTML(row.id)}">${tr("Remove")}</button>` : ""}</li>`;
}

function downloadQueueHTML(rows) {
  const labels = {
    waiting: tr("Waiting"),
    downloading: tr("Downloading"),
    completed: tr("Completed"),
    error: tr("Failed"),
  };
  return `<div class="section-heading"><div><h2>${tr("Download queue")}</h2><p>${tr("Queued batches run one at a time, with tracks downloading in parallel. You can keep browsing. Keep Acervo open until the queue finishes.")}</p></div></div>${rows.length ? `<ol class="download-queue-list">${rows.map((row) => downloadQueueItem(row, labels)).join("")}</ol>` : `<p class="download-hint">${tr("Your download queue is empty.")}</p>`}`;
}

function updateDownloadQueue() {
  const rows = state.summary?.download_queue || [],
    pending = rows.filter((row) => ["waiting", "downloading"].includes(row.status)).length;
  let badge = $("#download-queue-count");
  if (!badge) {
    badge = document.createElement("span");
    badge.id = "download-queue-count";
    badge.className = "nav-counter";
    $('.nav-item[data-view="downloads"]').append(badge);
  }
  badge.hidden = !pending;
  badge.textContent = number(pending);
  badge.setAttribute("aria-label", tr("Download queue") + ": " + number(pending));
  const panel = $("#download-queue");
  if (panel) {
    const signature = JSON.stringify([uiLocale(), rows]);
    if (panel._queueSignature !== signature) {
      panel.innerHTML = downloadQueueHTML(rows);
      panel._queueSignature = signature;
    }
  }
}

function setCatalogControls() {
  const readOnly = state.serverIncompatible,
    blocked = readOnly || !!state.summary?.protection_error || simpleDownloadState.enqueuing;
  document.querySelectorAll('#simple-download-form [type="submit"],[data-simple-open]').forEach((button) => {
    button.disabled = readOnly || simpleDownloadState.loading || button.getAttribute("aria-busy") === "true";
  });
  document.querySelectorAll('[data-action="simple-download"]').forEach((button) => {
    button.disabled = blocked;
  });
}

function simpleDownloadResult(row) {
  return `<li><strong>${escapeHTML(row.artist)} — ${escapeHTML(row.title)}</strong><span class="${["error", "not_downloaded"].includes(row.status) ? "download-warning" : "source"}">${escapeHTML(row.status === "downloaded" ? tr("Saved in") + " " + row.file : row.status === "not_downloaded" ? tr("Not downloaded.") + " " + (row.message || tr("Batch interrupted.")) : row.message || tr("Already in folder"))}</span>${row.filled_genre ? `<span class="source">${tr("Genre:")} ${escapeHTML(row.filled_genre)}</span>` : ""}</li>`;
}

function simpleDownloadResults(rows) {
  if (!rows?.length) return "";
  const counts = { downloaded: 0, already_existed: 0, error: 0, not_downloaded: 0 };
  for (const row of rows) counts[row.status] = (counts[row.status] || 0) + 1;
  return `<section class="download-panel"><div class="section-heading"><div><h2>${tr("Download results")}</h2><p>${number(counts.downloaded)} ${tr("downloaded ·")} ${number(counts.already_existed)} ${tr("already present ·")} ${number(counts.error)} ${tr("failed")}${counts.not_downloaded ? ` · ${number(counts.not_downloaded)} ${tr("not downloaded")}` : ""}</p></div></div><ul class="simple-result-list">${rows.map(simpleDownloadResult).join("")}</ul></section>`;
}

async function renderSimpleDownloads() {
  const request = state.request;
  let catalog = simpleDownloadState.catalogId ? await storedCatalog(simpleDownloadState.catalogId) : null;
  if (!simpleDownloadState.folder) {
    const info = await api("/api/download");
    if (request !== state.request) return "";
    simpleDownloadState.folder = info.folder;
    catalog = catalog || info.catalog;
  }
  if (request !== state.request) return "";
  if (catalog) useCatalog(catalog);
  const mode = simpleDownloadState.mode,
    [label, placeholder] = simpleModeLabels[mode].map(tr);
  return `${heading(tr("CHOOSE WHAT TO LISTEN TO"), tr("Download music"), tr("Find the album, choose the edition and download only the tracks you want."))}<form id="simple-download-form" class="download-panel"><div class="tabs simple-search-tabs">${[
    ["artist", tr("By artist")],
    ["song", tr("By track")],
    ["link", tr("By link")],
  ]
    .map(
      ([key, text]) =>
        `<button type="button" class="tab ${mode === key ? "active" : ""}" data-simple-mode="${key}" aria-pressed="${mode === key}">${text}</button>`,
    )
    .join(
      "",
    )}</div><div class="simple-search-fields"><label><span id="simple-query-label">${label}</span><input class="field" name="query" value="${escapeHTML(simpleDownloadState.query)}" placeholder="${escapeHTML(placeholder)}" required maxlength="1200"></label><button class="button primary" type="submit" ${simpleDownloadState.loading ? "disabled" : ""}>${icon("search")}${tr(simpleDownloadState.loading ? "Loading…" : mode === "link" ? "View tracks" : "Search")}</button></div><div class="simple-settings"><label>${tr("Save to folder")}<input class="field" name="folder" value="${escapeHTML(simpleDownloadState.folder)}" required></label><label>MP3<select class="field" name="bitrate">${["320k", "256k", "192k", "128k"].map((value) => `<option value="${value}" ${simpleDownloadState.bitrate === value ? "selected" : ""}>${value.slice(0, -1)} kbps</option>`).join("")}</select></label></div><p class="download-hint">${tr("Files go directly to the selected folder. Existing files are preserved. The MP3 bitrate does not improve source quality.")}</p></form>${simpleDownloadState.error ? `<p class="error-box">${escapeHTML(simpleDownloadState.error)}</p>` : ""}${simpleDownloadState.loading ? `<p role="status">${tr("Loading…")}</p>` : catalog ? catalogContent(catalog) : ""}<section class="download-panel" id="download-queue"></section>`;
}

async function runCatalogSearch() {
  const serial = ++simpleDownloadState.serial;
  const query = { type: simpleDownloadState.mode, query: simpleDownloadState.query };
  simpleDownloadState.loading = true;
  simpleDownloadState.error = "";
  simpleDownloadState.parents = [];
  await render();
  try {
    if (serial !== simpleDownloadState.serial || state.view !== "downloads") return;
    const catalog = await browseCatalog("/api/catalog/search", query);
    if (serial !== simpleDownloadState.serial || state.view !== "downloads") return;
    useCatalog(catalog);
  } catch (error) {
    if (serial === simpleDownloadState.serial) simpleDownloadState.error = error.message;
  } finally {
    if (serial === simpleDownloadState.serial) {
      simpleDownloadState.loading = false;
      if (state.view === "downloads") await render();
    }
  }
}

async function openSimpleCatalog(button) {
  const serial = ++simpleDownloadState.serial,
    request = state.request,
    old = simpleDownloadState.catalogId;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.title = tr("Loading…");
  try {
    const catalog = await browseCatalog("/api/catalog/open", { catalog: old, item: button.dataset.simpleOpen });
    if (serial !== simpleDownloadState.serial || request !== state.request) return;
    simpleDownloadState.parents.push(old);
    useCatalog(catalog);
    await render();
  } catch (error) {
    if (serial === simpleDownloadState.serial && request === state.request) throw error;
  } finally {
    if (button.isConnected) {
      button.removeAttribute("aria-busy");
      button.removeAttribute("title");
      setCatalogControls();
    }
  }
}

document.addEventListener("input", (event) => {
  if (event.target.closest("#simple-download-form")) simpleFormValues(event.target.closest("form"));
});
document.addEventListener("change", (event) => {
  if (event.target.matches(".simple-track-check")) {
    if (event.target.checked) simpleDownloadState.selected.add(event.target.value);
    else simpleDownloadState.selected.delete(event.target.value);
    updateSimpleSelection();
  }
  if (event.target.closest("#simple-download-form")) simpleFormValues(event.target.closest("form"));
});
document.addEventListener("submit", (event) => {
  if (event.target.id === "simple-download-form") {
    event.preventDefault();
    simpleFormValues(event.target);
    runCatalogSearch().catch((error) => toast(error.message));
  }
});
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button || button.disabled) return;
  try {
    if (button.dataset.simpleVersion) {
      simpleDownloadState.versionFilter = button.dataset.simpleVersion;
      applySimpleVersionFilter();
    } else if (button.dataset.simpleMode) {
      simpleFormValues($("#simple-download-form"));
      simpleDownloadState.serial++;
      simpleDownloadState.loading = false;
      simpleDownloadState.mode = button.dataset.simpleMode;
      simpleDownloadState.query = "";
      await render();
    } else if (button.dataset.simpleOpen) await openSimpleCatalog(button);
    else if (button.dataset.action === "simple-back") {
      const id = simpleDownloadState.parents.pop();
      if (id) {
        useCatalog(await storedCatalog(id));
        await render();
      }
    } else if (["simple-select-new", "simple-select-all", "simple-clear"].includes(button.dataset.action)) {
      const clear = button.dataset.action === "simple-clear";
      if (clear) simpleDownloadState.selected.clear();
      document.querySelectorAll(".simple-track-check").forEach((input) => {
        const row = input.closest(".simple-track");
        if (row.hidden && !clear) return;
        input.checked =
          button.dataset.action === "simple-select-all" ||
          (button.dataset.action === "simple-select-new" && row.dataset.existing !== "true");
        if (input.checked) simpleDownloadState.selected.add(input.value);
        else simpleDownloadState.selected.delete(input.value);
      });
      updateSimpleSelection();
    } else if (button.dataset.action === "simple-download") {
      const ids = simpleVisibleSelection();
      if (!ids.length) throw new Error(tr("Select at least one visible track in this filter to download."));
      simpleFormValues($("#simple-download-form"));
      simpleDownloadState.enqueuing = true;
      setCatalogControls();
      try {
        await api("/api/download/download", {
          catalog: simpleDownloadState.catalogId,
          ids,
          folder: simpleDownloadState.folder,
          bitrate: simpleDownloadState.bitrate,
        });
        await updateState();
        toast(tr("Added to the download queue."));
      } finally {
        simpleDownloadState.enqueuing = false;
        setCatalogControls();
      }
    } else if (button.dataset.removeDownload) {
      button.disabled = true;
      await api("/api/download/queue/remove", { id: button.dataset.removeDownload });
      await updateState();
    }
  } catch (error) {
    button.disabled = false;
    toast(error.message);
  }
});
