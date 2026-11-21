"use strict";

const playbackSessionState = {
  loaded: false,
  ready: false,
  enabled: true,
  library: null,
  loading: null,
  suspended: false,
  restoreWarned: false,
  restoring: null,
  timer: null,
  stamp: 0,
  lastTime: 0,
  lastBody: null,
  queueBody: null,
  queueRevision: 0,
  writes: Promise.resolve(),
  warned: false,
};

function playbackCheckpoint() {
  const saved = playbackSessionState;
  if (saved.restoring && saved.restoring.serial !== playback.serial) saved.restoring = null;
  if (!saved.ready || !saved.enabled || saved.suspended || saved.restoring || saved.library !== state.summary?.library)
    return undefined;
  if (saved.loading && !state.current) return undefined;
  if (state.current?.id.startsWith("stage-")) return undefined;
  if (!state.current) return null;
  if (!playback.base.includes(state.current.id) || !playback.order.includes(state.current.id)) return undefined;
  const queueBody = JSON.stringify([playback.base, playback.order]);
  if (queueBody !== saved.queueBody) {
    saved.queueBody = queueBody;
    saved.queueRevision = saved.stamp = Math.max(Date.now(), saved.stamp + 1);
  }
  return {
    track_id: state.current.id,
    position_seconds: Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
    queue: [...playback.base],
    order: [...playback.order],
    shuffle: playback.shuffle,
    repeat: playback.repeat,
    volume: audio.volume,
    muted: audio.muted,
    queue_revision: saved.queueRevision,
  };
}

function sessionPayload(fields) {
  const saved = playbackSessionState;
  saved.stamp = Math.max(Date.now(), saved.stamp + 1);
  return { library_path: saved.library, checkpoint_at: saved.stamp, ...fields };
}

function sessionWriteFailed(error) {
  playbackSessionState.lastBody = null;
  if (!playbackSessionState.warned) {
    playbackSessionState.warned = true;
    toast(tr("Could not save the playback session. Acervo will try again.") + " " + error.message);
  }
}

async function flushPlaybackSession(keepalive = false) {
  const saved = playbackSessionState;
  clearTimeout(saved.timer);
  const session = playbackCheckpoint();
  if (session === undefined) return;
  const body = JSON.stringify(session);
  if (body === saved.lastBody) return;
  saved.lastBody = body;
  saved.lastTime = Date.now();
  const payload = sessionPayload({ session });
  const write = () => api("/api/playback/session", payload, { keepalive: keepalive && body.length < 60000 });
  // The closing checkpoint may overtake an earlier request. The server rejects older stamps.
  const request = keepalive ? write() : saved.writes.catch(() => {}).then(write);
  saved.writes = request;
  if (keepalive && body.length >= 60000 && session) {
    // Browsers limit closing keepalive requests to 64 KiB. The full queue uses the normal
    // request; a compact checkpoint preserves the latest position if it is interrupted.
    const { queue, order, ...progress } = session;
    api("/api/playback/session", sessionPayload({ progress }), { keepalive: true }).catch(sessionWriteFailed);
  }
  try {
    await request;
    saved.warned = false;
  } catch (error) {
    sessionWriteFailed(error);
    throw error;
  }
}

function schedulePlaybackSession() {
  clearTimeout(playbackSessionState.timer);
  playbackSessionState.timer = setTimeout(() => flushPlaybackSession().catch(() => {}), 200);
}

function restorePlaybackPosition() {
  const saved = playbackSessionState,
    pending = saved.restoring;
  if (!pending) return;
  if (pending.serial !== playback.serial || pending.id !== state.current?.id) {
    saved.restoring = null;
    return;
  }
  if (audio.readyState < 1) return;
  audio.currentTime = Math.min(pending.position, Number.isFinite(audio.duration) ? audio.duration : pending.position);
  $("#time-current").textContent = duration(audio.currentTime);
  $("#seek").value = audio.duration > 0 ? (audio.currentTime / audio.duration) * 1000 : 0;
  saved.restoring = null;
  schedulePlaybackSession();
}

async function initializePlaybackSession() {
  const saved = playbackSessionState;
  if (saved.ready || saved.loading || saved.suspended) return saved.loading;
  const serial = playback.serial,
    library = state.summary.library;
  saved.loading = (async () => {
    try {
      const data = await api("/api/playback/session");
      if (state.summary.library !== library || data.library_path !== library) return;
      saved.library = library;
      saved.enabled = data.enabled;
      saved.stamp = Math.max(saved.stamp, data.checkpoint_at || 0);
      saved.loaded = true;
      if (!data.ready) return;
      saved.ready = true;
      if (!data.enabled || !data.session || serial !== playback.serial || state.current) return;
      const session = data.session;
      const [{ tracks }, track] = await Promise.all([
        api("/api/queue"),
        api(`/api/track?id=${encodeURIComponent(session.track_id)}`),
      ]);
      if (
        serial !== playback.serial ||
        state.current ||
        state.summary.library !== library ||
        saved.suspended ||
        !saved.enabled
      )
        return;
      const known = new Map(tracks.map((row) => [row.id, row]));
      if (!known.has(track.id)) return;
      saved.restoring = { serial, id: track.id, position: session.position_seconds };
      playback.base = session.queue.filter((id) => known.has(id));
      playback.order = session.order.filter((id) => known.has(id));
      saved.queueBody = JSON.stringify([playback.base, playback.order]);
      saved.queueRevision = session.queue_revision || 0;
      playback.tracks = known;
      playback.shuffle = session.shuffle;
      playback.repeat = session.repeat;
      audio.volume = session.volume;
      audio.muted = session.muted;
      $("#volume").value = Math.round(session.volume * 100);
      loadPlayerTrack(track);
      restorePlaybackPosition();
    } catch (error) {
      if (!state.current) saved.ready = false;
      if (!saved.restoreWarned) toast(tr("Could not restore the last playback session.") + " " + error.message);
      saved.restoreWarned = true;
    } finally {
      saved.loading = null;
    }
  })();
  return saved.loading;
}

async function setPlaybackSessionEnabled(enabled) {
  const saved = playbackSessionState;
  if (!saved.loaded) await initializePlaybackSession();
  if (!saved.loaded) throw new Error(tr("Could not restore the last playback session."));
  await saved.writes.catch(() => {});
  await api("/api/playback/session", sessionPayload({ enabled }));
  saved.enabled = enabled;
  saved.lastBody = null;
  if (enabled) await flushPlaybackSession();
}

function playbackSessionSettings() {
  return `<div class="form-row"><label><input type="checkbox" class="check" id="restore-playback" ${playbackSessionState.enabled ? "checked" : ""}> ${tr("Restore the last session on startup")}</label><p>${tr("Remembers the track, position, queue and playback controls for this library. Returns paused. Disabling clears the saved session.")}</p></div>`;
}

audio.addEventListener("loadedmetadata", restorePlaybackPosition);
for (const event of ["play", "pause", "seeked", "volumechange", "ended"])
  audio.addEventListener(event, schedulePlaybackSession);
audio.addEventListener("timeupdate", () => {
  if (Date.now() - playbackSessionState.lastTime >= 5000) flushPlaybackSession().catch(() => {});
});
audio.addEventListener("error", () => {
  playbackSessionState.restoring = null;
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) flushPlaybackSession(true).catch(() => {});
});
window.addEventListener("pagehide", () => flushPlaybackSession(true).catch(() => {}));
