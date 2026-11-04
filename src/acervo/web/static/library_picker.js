"use strict";

const LIBRARY_KEY = "acervo-library";
const libraryPicker = {serial:0, opening:false};

function rememberedLibrary() {
  try { return localStorage.getItem(LIBRARY_KEY); } catch { return null; }
}

function rememberLibrary(path) {
  try {
    if (path) localStorage.setItem(LIBRARY_KEY, path);
    else localStorage.removeItem(LIBRARY_KEY);
  } catch {}
}

async function openRememberedLibrary() {
  const remembered = rememberedLibrary();
  if (!remembered || state.summary.fixed_folder || remembered === state.summary.library) return;
  try {
    await api("/api/folders",{path:remembered});
  } catch (error) {
    // Only an answer about the folder itself forgets it; a temporary failure must not.
    if (error.status === 400) {
      rememberLibrary(null);
      toast(tr("The folder you used last time is not available. Opening the default one."));
    } else {
      toast(error.message);
    }
    return;
  }
  try {
    await api("/api/library/open",{folder:remembered});
    await updateState();
  } catch (error) {
    toast(error.message);
  }
}

function libraryPickerBody(data) {
  const folders = data.folders.map(folder=>`<button type="button" class="library-folder" data-library-folder="${escapeHTML(folder.path)}" data-i18n-user="true">${icon("folder")}<span>${escapeHTML(folder.name)}</span></button>`).join("");
  return `${modalHead(tr("Choose your music folder"),tr("Acervo reads this folder. Nothing is moved or renamed, and this browser remembers your choice."))}<div class="modal-body"><form id="library-path-form" class="library-path"><button type="button" class="button small ghost" data-library-folder="${escapeHTML(data.parent||"")}" ${data.parent?"":"disabled"} aria-label="${tr("Parent folder")}" title="${tr("Parent folder")}">${icon("arrow")}</button><input id="library-path" class="field" type="text" spellcheck="false" autocomplete="off" aria-label="${tr("Folder path")}" value="${escapeHTML(data.path)}"><button class="button small ghost">${tr("Go")}</button></form><div class="library-folders">${folders||`<p class="queue-empty">${tr("No subfolders here")}</p>`}</div><p class="library-note">${tr("Opening another folder stops the current track.")}</p></div><div class="modal-actions"><button class="button ghost" data-action="close-modal">${tr("Cancel")}</button><button class="button primary" data-library-open="${escapeHTML(data.path)}">${tr("Open this folder")}</button></div>`;
}

async function showLibraryPicker(path) {
  const serial = ++libraryPicker.serial, place = `${state.view}${location.hash}`, dialogOpen = $('#modal').open;
  let data;
  try {
    data = await api("/api/folders",{path:path||state.summary.library});
  } catch (error) {
    if (serial === libraryPicker.serial) throw error;
    return;
  }
  // The answer may arrive after leaving this page, closing the dialog or opening another one.
  if (serial === libraryPicker.serial && place === `${state.view}${location.hash}` && dialogOpen === $('#modal').open) modal(libraryPickerBody(data));
}

async function openChosenLibrary(button) {
  const buttons = [...$('#modal-content').querySelectorAll("button")].filter(item=>!item.disabled);
  buttons.forEach(item=>{ item.disabled = true; });
  libraryPicker.opening = true;
  button.textContent = tr("Opening…");
  try {
    const {library} = await api("/api/library/open",{folder:button.dataset.libraryOpen});
    rememberLibrary(library);
    location.hash = "home";
    location.reload();
  } catch (error) {
    libraryPicker.opening = false;
    buttons.forEach(item=>{ item.disabled = false; });
    button.textContent = tr("Open this folder");
    toast(error.message);
  }
}

document.addEventListener("click",event=>{
  const button = event.target.closest("button");
  if (!button || button.disabled) return;
  if (button.dataset.action === "choose-library") showLibraryPicker().catch(error=>toast(error.message));
  else if (button.dataset.libraryFolder) showLibraryPicker(button.dataset.libraryFolder).catch(error=>toast(error.message));
  else if (button.dataset.libraryOpen) openChosenLibrary(button);
});
$('#modal').addEventListener("close",()=>{ libraryPicker.serial++; });
$('#modal').addEventListener("cancel",event=>{ if (libraryPicker.opening) event.preventDefault(); });
document.addEventListener("submit",event=>{
  if (event.target.id !== "library-path-form" || libraryPicker.opening) return;
  event.preventDefault();
  showLibraryPicker($('#library-path').value.trim()).catch(error=>toast(error.message));
});
