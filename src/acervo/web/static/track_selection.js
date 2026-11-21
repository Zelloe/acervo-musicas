"use strict";

const TRACK_SELECTION_LIMIT = 500;
const trackSelection = { ids: new Set(), generation: 0, library: null, revision: null, opening: null };

function trackSelectionCell(track) {
  return `<td class="track-select-cell"><label class="track-select-target"><input type="checkbox" class="check" data-track-select="${escapeHTML(track.id)}" aria-label="${escapeHTML(tr("Select track: {title}").replace("{title}", () => trackTitle(track)))}" ${trackSelection.ids.has(track.id) ? "checked" : ""}></label></td>`;
}

function trackSelectionHeader() {
  return `<th class="track-select-cell"><label class="track-select-target"><input type="checkbox" class="check" data-track-select-visible aria-label="${tr("Select visible tracks")}" title="${tr("Select visible tracks")}"></label></th>`;
}

function visibleTrackCheckboxes() {
  return [...document.querySelectorAll("#content [data-track-select]")];
}

function updateTrackSelectionControls(blocked) {
  document.querySelectorAll("[data-edit-selected]").forEach((button) => {
    button.disabled = blocked || !trackSelection.ids.size || !!trackSelection.opening;
  });
}

function renderTrackSelection() {
  const boxes = visibleTrackCheckboxes(),
    visible = new Set(boxes.map((box) => box.dataset.trackSelect)),
    checked = [...visible].filter((id) => trackSelection.ids.has(id)).length;
  boxes.forEach((box) => {
    box.checked = trackSelection.ids.has(box.dataset.trackSelect);
    box.closest("tr").classList.toggle("track-row-selected", box.checked);
  });
  document.querySelectorAll("[data-track-select-visible]").forEach((box) => {
    box.checked = visible.size > 0 && checked === visible.size;
    box.indeterminate = checked > 0 && checked < visible.size;
  });
  const bar = $("#track-selection-bar");
  if (!bar) return;
  const count = trackSelection.ids.size,
    hidden = count - checked;
  bar.hidden =
    !count ||
    !(
      ["home", "library", "search", "artist", "album"].includes(state.view) ||
      (state.view === "review" && boxes.length > 0)
    );
  bar.innerHTML = count
    ? `<span role="status"><strong>${escapeHTML(tr("Selected tracks: {count}").replace("{count}", number(count)))}</strong>${hidden ? `<br>${escapeHTML(tr("{count} outside this view").replace("{count}", number(hidden)))}` : ""}</span><div class="tabs"><button class="text-button" data-clear-track-selection>${tr("Clear selection")}</button><button class="button primary" data-edit-selected data-manual-write>${icon("edit")}${tr(trackSelection.opening ? "Opening…" : "Edit selected tracks")}</button></div>`
    : "";
  setWriteControls();
}

function refreshTrackSelection() {
  if (!state.summary) return;
  const library = state.summary.library,
    revision = state.summary.library_revision;
  if (trackSelection.library !== library) {
    trackSelection.ids.clear();
    trackSelection.generation++;
    trackSelection.library = library;
    trackSelection.revision = null;
  }
  if (trackSelection.revision !== revision) {
    trackSelection.revision = revision;
    if (trackSelection.ids.size) {
      const captured = [...trackSelection.ids];
      api("/api/queue")
        .then(({ tracks }) => {
          if (trackSelection.library !== library || state.summary.library_revision !== revision) return;
          const known = new Set(tracks.map((track) => track.id));
          let removed = 0;
          captured.forEach((id) => {
            if (!known.has(id) && trackSelection.ids.delete(id)) removed++;
          });
          if (removed) trackSelection.generation++;
          renderTrackSelection();
        })
        .catch(() => {
          if (trackSelection.library === library && trackSelection.revision === revision)
            trackSelection.revision = null;
        });
    }
  }
  renderTrackSelection();
}

function changeTrackSelection(ids, selected) {
  const next = new Set(trackSelection.ids);
  for (const id of ids) {
    if (selected) next.add(id);
    else next.delete(id);
  }
  if (next.size > TRACK_SELECTION_LIMIT) {
    toast(tr("Select up to 500 tracks at a time."));
    renderTrackSelection();
    return;
  }
  trackSelection.ids = next;
  trackSelection.generation++;
  renderTrackSelection();
}

async function editSelectedTracks() {
  if (!trackSelection.ids.size || trackSelection.opening) return;
  const ids = [...trackSelection.ids],
    generation = trackSelection.generation,
    opening = {};
  trackSelection.opening = opening;
  renderTrackSelection();
  try {
    const editor = await manualRead("/api/tracks/batch/editor", { ids });
    if (editor && generation === trackSelection.generation) showCommonEditForm(editor);
  } catch (error) {
    if (generation === trackSelection.generation) toast(error.message);
  } finally {
    if (trackSelection.opening === opening) trackSelection.opening = null;
    renderTrackSelection();
  }
}

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-track-select]")) {
    changeTrackSelection([event.target.dataset.trackSelect], event.target.checked);
  } else if (event.target.matches("[data-track-select-visible]")) {
    changeTrackSelection(
      visibleTrackCheckboxes().map((box) => box.dataset.trackSelect),
      event.target.checked,
    );
  }
});
document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || button.disabled) return;
  if (button.hasAttribute("data-edit-selected")) editSelectedTracks();
  else if (button.hasAttribute("data-clear-track-selection")) changeTrackSelection([...trackSelection.ids], false);
});
