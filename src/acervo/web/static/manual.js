"use strict";

const MANUAL_FIELDS = [
  ["title", "Title"],
  ["artist", "Artist"],
  ["album_artist", "Album artist"],
  ["album", "Album"],
  ["genre", "Genre"],
  ["year", "Year"],
  ["track", "Track"],
  ["disc", "Disc"],
];
const manualState = { editor: null, track: null, preview: null, image: null, serial: 0 };
let trackMenu = null;
let trackMenuAnchor = null;

function closeTrackMenu(restoreFocus = false) {
  trackMenu?.remove();
  trackMenu = null;
  trackMenuAnchor?.setAttribute("aria-expanded", "false");
  if (restoreFocus && trackMenuAnchor?.isConnected) trackMenuAnchor.focus();
  trackMenuAnchor = null;
}

function positionTrackMenu() {
  if (!trackMenu) return;
  if (!trackMenuAnchor?.isConnected) {
    closeTrackMenu();
    return;
  }
  const rect = trackMenuAnchor.getBoundingClientRect();
  trackMenu.style.left = `${Math.max(8, Math.min(rect.right - trackMenu.offsetWidth, innerWidth - trackMenu.offsetWidth - 8))}px`;
  trackMenu.style.top = `${Math.max(8, Math.min(rect.bottom + 4, innerHeight - trackMenu.offsetHeight - 8))}px`;
}

function openTrackMenu(button) {
  const wasOpen = trackMenuAnchor === button;
  closeTrackMenu();
  if (wasOpen) return;
  trackMenuAnchor = button;
  button.setAttribute("aria-expanded", "true");
  const menu = document.createElement("div");
  menu.className = "track-menu";
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", tr("Track actions"));
  const items = [
    ["play", "play", "Listen"],
    ["next", "next", "Play next"],
    ["queue", "music", "Add to queue"],
    ["edit", "edit", "Edit tags"],
    ["cover", "image", "Add missing cover"],
    ["details", "tag", "Track details"],
    ["delete", "trash", "Delete track"],
  ];
  menu.innerHTML = items
    .map(
      ([action, name, label]) =>
        `<button role="menuitem" tabindex="-1" data-manual="${action}" data-id="${escapeHTML(button.dataset.trackMenu)}" ${["edit", "cover", "delete"].includes(action) ? "data-manual-write" : ""} class="${action === "delete" ? "danger-text" : ""}">${icon(name)}${tr(label)}</button>`,
    )
    .join("");
  document.body.append(menu);
  trackMenu = menu;
  setWriteControls();
  positionTrackMenu();
  menu.querySelector("button:not(:disabled)")?.focus({ preventScroll: true });
}

function manualFooter(label = "Preview") {
  return `<div class="modal-actions"><button type="button" class="button ghost" data-action="close-modal">${tr("Cancel")}</button><button type="submit" class="button primary" data-manual-write>${tr(label)} ${icon("arrow")}</button></div>`;
}

async function showManualEditor(id) {
  const serial = ++manualState.serial;
  const editor = await api(`/api/tracks/editor?id=${encodeURIComponent(id)}`);
  if (serial !== manualState.serial) return;
  manualState.editor = editor;
  manualState.preview = null;
  modal(
    `${modalHead(tr("Edit tags"), escapeHTML(editor.file.split(/[\\/]/).pop()))}<form id="manual-edit-form"><div class="modal-body"><p class="download-hint">${tr("Tags are optional. You can fill in empty fields. Use one line per value; leave a field empty to clear it.")}</p><div class="manual-fields">${MANUAL_FIELDS.map(([field, label]) => `<div><label for="manual-${field}">${tr(label)}${editor.protected_fields.includes(field) ? ` · ${tr("Protected")}` : ""}</label><textarea class="field" id="manual-${field}" name="${field}" rows="2" ${editor.protected_fields.includes(field) ? "disabled" : ""}>${escapeHTML((editor.fields[field] || []).join("\n"))}</textarea></div>`).join("")}</div><p class="download-hint">${tr("Protected fields can be unlocked in Track details.")}</p></div>${manualFooter()}</form>`,
  );
}

async function showCoverEditor(id) {
  const serial = ++manualState.serial;
  const track = await api(`/api/track?id=${encodeURIComponent(id)}`);
  if (serial !== manualState.serial) return;
  manualState.track = track;
  manualState.image = null;
  const canAlbum = join(track.album).trim() && (join(track.album_artist).trim() || join(track.artist).trim());
  modal(
    `${modalHead(tr("Add missing cover"), escapeHTML(trackTitle(track)))}<form id="manual-cover-form"><div class="modal-body"><label for="manual-image">${tr("Choose a JPEG or PNG image up to 5 MB")}</label><input class="field" type="file" id="manual-image" accept="image/jpeg,image/png" required><img id="manual-image-preview" class="manual-cover-preview" alt="${tr("Selected cover")}" hidden>${canAlbum ? `<label class="manual-album-option"><input type="checkbox" class="check" id="manual-cover-album"> ${tr("Also fill missing covers in this album")} · ${escapeHTML(join(track.album))}</label>` : ""}<p class="download-hint">${tr("The image will be embedded in the audio file. Existing covers are preserved.")}</p></div>${manualFooter()}</form>`,
  );
}

function showManualPreview(preview) {
  manualState.preview = preview;
  const removing = preview.kind === "delete";
  const title = removing ? "Delete this track?" : preview.kind === "cover" ? "Review cover" : "Review tag changes";
  const detail = removing
    ? "The audio file will leave your music folder. Its original will be kept outside the library; restore it from History."
    : "A full original backup will be kept. You can undo this operation from History.";
  modal(
    `${modalHead(tr(title), tr(detail))}<div class="modal-body">${preview.kind === "cover" ? `<img class="manual-cover-preview" src="${manualState.image}" alt="${tr("Selected cover")}"><p>${number(preview.count)} ${tr("files")} · ${number(preview.skipped)} ${tr("existing covers preserved")}</p>` : ""}<p class="download-hint">${tr("Backup size")}: ${bytes(preview.size_bytes)}</p>${preview.changes ? `<dl class="details-list">${preview.changes.map((change) => `<div><dt>${tr(MANUAL_FIELDS.find(([key]) => key === change.field)?.[1] || change.field)}</dt><dd><span class="change-old">${escapeHTML(join(change.current) || tr("Not set"))}</span><span class="change-new">${escapeHTML(join(change.proposed) || tr("Not set"))}</span></dd></div>`).join("")}</dl>` : ""}<ul class="confirm-list">${preview.sample.map((track) => `<li><strong>${escapeHTML(trackTitle(track))}</strong><span class="manual-file">${escapeHTML(track.file)}</span></li>`).join("")}</ul>${preview.count > preview.sample.length ? `<p>${tr("and {count} more").replace("{count}", number(preview.count - preview.sample.length))}</p>` : ""}</div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Cancel")}</button><button class="button ${removing ? "danger" : "primary"}" data-manual="save" data-manual-write>${tr(removing ? "Delete track" : "Back up and save")}</button></div>`,
  );
}

async function enqueueManual(id, next) {
  const track = await api(`/api/track?id=${encodeURIComponent(id)}`);
  if (!state.current) {
    setQueue([track], id);
    await startTrack(id);
    return;
  }
  if (id === state.current.id) return;
  playback.tracks.set(id, track);
  playback.order = playback.order.filter((item) => item !== id);
  playback.base = playback.base.filter((item) => item !== id);
  playback.index = playback.order.indexOf(state.current.id);
  playback.order.splice(next ? playback.index + 1 : playback.order.length, 0, id);
  const baseIndex = playback.base.indexOf(state.current.id);
  playback.base.splice(next ? baseIndex + 1 : playback.base.length, 0, id);
  updatePlayerControls();
  renderQueue();
  toast(tr(next ? "Track will play next." : "Track added to queue."));
}

document.addEventListener("click", async (event) => {
  const anchor = event.target.closest("[data-track-menu]");
  if (anchor) {
    openTrackMenu(anchor);
    return;
  }
  const button = event.target.closest("[data-manual]");
  if (!button) {
    if (!event.target.closest(".track-menu")) closeTrackMenu();
    return;
  }
  if (button.disabled) return;
  const { manual: action, id } = button.dataset;
  closeTrackMenu();
  try {
    if (action === "edit") await showManualEditor(id);
    else if (action === "cover") await showCoverEditor(id);
    else if (action === "delete") showManualPreview(await api("/api/tracks/delete/preview", { id }));
    else if (action === "details") await showDetails(id);
    else if (action === "play") await startPlayback({ dataset: { play: id }, closest: () => null });
    else if (action === "next" || action === "queue") await enqueueManual(id, action === "next");
    else if (action === "save" && manualState.preview) {
      button.disabled = true;
      await api("/api/tracks/save", { preview: manualState.preview.preview });
      manualState.preview = null;
      manualState.image = null;
      $("#modal").close();
      await updateState();
      toast(tr("Saving changes. Follow progress at the top."));
    }
  } catch (error) {
    button.disabled = false;
    toast(error.message);
  }
});

document.addEventListener("change", async (event) => {
  if (event.target.id !== "manual-image") return;
  const file = event.target.files[0];
  manualState.image = null;
  $("#manual-image-preview").hidden = true;
  if (!file) return;
  if (file.size > 5 * 1024 * 1024 || !["image/jpeg", "image/png"].includes(file.type)) {
    event.target.value = "";
    toast(tr("Choose a JPEG or PNG image up to 5 MB"));
    return;
  }
  try {
    const image = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error(tr("Could not read this image")));
      reader.readAsDataURL(file);
    });
    if (event.target !== $("#manual-image") || event.target.files[0] !== file) return;
    manualState.image = image;
    $("#manual-image-preview").src = image;
    $("#manual-image-preview").hidden = false;
  } catch (error) {
    toast(error.message);
  }
});

document.addEventListener("submit", async (event) => {
  if (!["manual-edit-form", "manual-cover-form"].includes(event.target.id)) return;
  event.preventDefault();
  const form = event.target;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    let preview;
    if (form.id === "manual-edit-form") {
      const editor = manualState.editor;
      const fields = {};
      for (const [field] of MANUAL_FIELDS) {
        const input = form.elements.namedItem(field);
        if (input.disabled || input.value === (editor.fields[field] || []).join("\n")) continue;
        fields[field] = input.value
          .split("\n")
          .map((value) => value.trim())
          .filter(Boolean);
      }
      preview = await api("/api/tracks/edit/preview", {
        id: editor.id,
        fields,
        previous_sha256: editor.previous_sha256,
      });
    } else {
      if (!manualState.image) throw new Error(tr("Choose a JPEG or PNG image up to 5 MB"));
      preview = await api("/api/tracks/cover/preview", {
        id: manualState.track.id,
        image: manualState.image.split(",")[1],
        album: $("#manual-cover-album")?.checked || false,
      });
    }
    showManualPreview(preview);
  } catch (error) {
    submit.disabled = false;
    toast(error.message);
  }
});

document.addEventListener("keydown", (event) => {
  if (!trackMenu) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeTrackMenu(true);
    return;
  }
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const items = [...trackMenu.querySelectorAll("button:not(:disabled)")];
  const index = items.indexOf(document.activeElement);
  const next =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? items.length - 1
        : (index + (event.key === "ArrowUp" ? -1 : 1) + items.length) % items.length;
  items[next]?.focus();
});
window.addEventListener("resize", positionTrackMenu);
window.addEventListener(
  "scroll",
  (event) => {
    if (!trackMenu?.contains(event.target)) positionTrackMenu();
  },
  true,
);
document.addEventListener("focusin", (event) => {
  if (trackMenu && !trackMenu.contains(event.target) && event.target !== trackMenuAnchor) closeTrackMenu();
});

async function refreshManualPlayback() {
  // Refresh the entire queue after a library revision, including jobs completed while the tab slept.
  if (!state.current && !playback.base.length) return;
  const { tracks } = await api("/api/queue");
  const known = new Map(tracks.map((track) => [track.id, track]));
  playback.base = playback.base.filter((id) => known.has(id));
  playback.order = playback.order.filter((id) => known.has(id));
  playback.tracks = new Map(playback.base.map((id) => [id, known.get(id)]));
  state.queue = state.queue.filter((id) => known.has(id));
  if (state.current && !state.current.id.startsWith("stage-")) {
    const currentId = state.current.id;
    const track = known.has(currentId) ? await api(`/api/track?id=${encodeURIComponent(currentId)}`) : null;
    if (state.current?.id !== currentId) return;
    if (track) {
      state.current = track;
      playback.tracks.set(track.id, track);
      $("#player-title").textContent = trackTitle(track);
      $("#player-artist").textContent = trackArtist(track);
      $("#player-cover").innerHTML = trackArtwork(track);
      if (typeof setLyricsTrack === "function") setLyricsTrack(track);
    } else {
      ++playback.serial;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      state.current = null;
      $("#player-title").textContent = tr("Your next track awaits.");
      $("#player-artist").textContent = "";
      $("#player-cover").innerHTML = icon("disc");
      $("#audio-quality").textContent = "";
      $("#seek").value = 0;
      $("#time-current").textContent = "0:00";
      $("#time-total").textContent = "0:00";
      $("#play-toggle").disabled = true;
      $("#seek").disabled = true;
      $("#now-toggle").disabled = true;
      closeNowView();
      if (typeof closeLyrics === "function") closeLyrics();
      if (typeof setLyricsTrack === "function") setLyricsTrack(null);
    }
  }
  playback.index = playback.order.indexOf(state.current?.id);
  updatePlayerControls();
  renderNowView();
}
