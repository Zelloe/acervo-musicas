"use strict";

const ALBUM_EDIT_FIELDS = MANUAL_FIELDS.filter(([field]) => !["title", "track"].includes(field));
const albumEditState = { editor: null };

function albumEditRows(editor) {
  return ALBUM_EDIT_FIELDS.map(([field, label]) => {
    const status = editor.field_status[field],
      locked = status.protected_count === editor.track_count,
      values = editor.fields[field] || [];
    return `<tr data-album-edit-row="${field}"><th scope="row"><label><input type="checkbox" class="check" data-album-field="${field}" ${locked ? "disabled" : ""}> ${escapeHTML(tr(label))}</label>${status.protected_count ? `<span class="tag-protected">${icon("lock")}${escapeHTML(tr("Protected on {count} tracks").replace("{count}", number(status.protected_count)))}</span>` : ""}</th><td class="tag-current">${status.mixed ? `<span class="source">${tr("Multiple values")}</span>` : escapeHTML(values.join("\n") || tr("Not set"))}</td><td class="tag-proposed"><textarea class="field" id="album-edit-${field}" name="${field}" aria-label="${escapeHTML(tr(label))}" rows="2" disabled>${escapeHTML(values.join("\n"))}</textarea></td></tr>`;
  }).join("");
}

async function showAlbumEditor(id) {
  const editor = await manualRead(`/api/albums/editor?id=${encodeURIComponent(id)}`);
  if (!editor) return;
  showCommonEditForm(editor);
}

function showCommonEditForm(editor) {
  const batch = editor.kind === "batch-edit";
  albumEditState.editor = editor;
  manualState.preview = null;
  modal(
    `${modalHead(tr(batch ? "Edit selected tracks" : "Edit album tags"), batch ? `${number(editor.track_count)} ${tr("selected tracks")}` : `${escapeHTML(join(editor.artist))} · ${escapeHTML(join(editor.album))} · ${number(editor.track_count)} ${tr("tracks")}`)}<form id="album-edit-form"><div class="modal-body"><p class="download-hint">${tr(batch ? "Select only the fields you want to apply to the selected tracks. Titles and track numbers are kept. Protected values are preserved." : "Select only the fields you want to apply across this album. Titles and track numbers are kept. Protected values are preserved.")}</p><p class="download-hint">${tr("Use one line per value. Leaving a selected field empty clears it on unprotected tracks. Unselected fields stay unchanged.")}</p><fieldset id="album-edit-fields"><table class="tag-comparison album-edit-fields"><caption class="sr-only">${tr("Compare tags")}</caption><thead><tr><th scope="col">${tr("Apply field")}</th><th scope="col">${tr("Current value")}</th><th scope="col">${tr("New value")}</th></tr></thead><tbody>${albumEditRows(editor)}</tbody></table></fieldset></div>${manualFooter()}</form>`,
  );
}

function showAlbumEditPreview(preview) {
  const batch = preview.kind === "batch-edit";
  manualState.preview = preview;
  const rows = preview.track_changes
    .map((track) => {
      const kept = track.protected_fields.map((field) =>
        tr(MANUAL_FIELDS.find(([key]) => key === field)?.[1] || field),
      );
      return `<details class="album-edit-preview"><summary><strong>${escapeHTML(trackTitle(track))}</strong><span>${track.changes.length ? number(track.changes.length) + " " + tr(track.changes.length === 1 ? "field to change" : "fields to change") : tr("No changes")}</span></summary><p class="manual-file">${escapeHTML(track.file)}</p>${kept.length ? `<p class="download-hint">${escapeHTML(tr("Protected fields kept: {fields}").replace("{fields}", kept.join(", ")))}</p>` : ""}${track.changes.length ? tagComparison(track.changes) : ""}</details>`;
    })
    .join("");
  modal(
    `${modalHead(tr(batch ? "Review selected track changes" : "Review album changes"), batch ? `${number(preview.track_count)} ${tr("selected tracks")}` : `${escapeHTML(join(preview.artist))} · ${escapeHTML(join(preview.album))}`)}<div class="modal-body"><p><strong>${escapeHTML(tr("{count} of {total} tracks will change").replace("{count}", number(preview.count)).replace("{total}", number(preview.track_count)))}</strong></p><p class="download-hint">${escapeHTML(tr("Unchanged tracks: {count}").replace("{count}", number(preview.skipped)))} · ${escapeHTML(tr("Protected field changes skipped: {count}").replace("{count}", number(preview.protected_count)))}</p><p class="download-hint">${tr("A full original backup will be kept. You can undo this operation from History.")}</p><p class="download-hint">${tr("Backup size")}: ${bytes(preview.size_bytes)}</p>${rows}</div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Cancel")}</button><button class="button primary" data-manual="save" data-manual-write>${tr("Back up and save")}</button></div>`,
  );
}

function updateAlbumEditRow(checkbox) {
  const field = checkbox.dataset.albumField,
    form = checkbox.form;
  if (!form || form.dataset.manualPending) return;
  const input = form.elements.namedItem(field);
  input.disabled = !checkbox.checked;
  const editor = albumEditState.editor;
  checkbox
    .closest("tr")
    .classList.toggle(
      "tag-changed",
      checkbox.checked && (editor.field_status[field].mixed || input.value !== (editor.fields[field] || []).join("\n")),
    );
}

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-album-field]")) updateAlbumEditRow(event.target);
});
document.addEventListener("input", (event) => {
  if (event.target.form?.id !== "album-edit-form") return;
  const checkbox = event.target.closest("tr").querySelector("[data-album-field]");
  updateAlbumEditRow(checkbox);
});
document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (form.id !== "album-edit-form") return;
  event.preventDefault();
  if (form.dataset.manualPending) return;
  const editor = albumEditState.editor,
    fields = {};
  for (const checkbox of form.querySelectorAll("[data-album-field]:checked:not(:disabled)")) {
    const field = checkbox.dataset.albumField;
    fields[field] = form.elements
      .namedItem(field)
      .value.split("\n")
      .map((value) => value.trim())
      .filter(Boolean);
  }
  if (!Object.keys(fields).length) {
    toast(
      tr(
        editor.kind === "batch-edit"
          ? "Select at least one field to change."
          : "Select at least one album field to change.",
      ),
    );
    return;
  }
  const submit = form.querySelector('[type="submit"]'),
    fieldset = form.querySelector("fieldset");
  form.dataset.manualPending = "true";
  submit.disabled = true;
  fieldset.disabled = true;
  try {
    const batch = editor.kind === "batch-edit";
    const preview = await manualRead(
      batch ? "/api/tracks/batch/preview" : "/api/albums/edit/preview",
      batch
        ? {
            ids: editor.ids,
            batch_revision: editor.batch_revision,
            fields,
          }
        : { id: editor.id, album_revision: editor.album_revision, fields },
    );
    if (preview) showAlbumEditPreview(preview);
  } catch (error) {
    submit.disabled = false;
    toast(error.message);
  } finally {
    delete form.dataset.manualPending;
    fieldset.disabled = false;
    if (form.isConnected) setWriteControls();
  }
});
