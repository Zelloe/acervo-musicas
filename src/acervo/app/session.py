"""Playback preferences and checkpoints outside the music library."""

import hashlib
import json
import math
from pathlib import Path

from acervo.app.common import readable_music
from acervo.library.core import atomic_json
from acervo.messages import _, register
from acervo.paths import DATA

register(
    {
        "The open library changed; reload before saving playback.": "A biblioteca aberta mudou; recarregue antes de salvar a reprodução.",
    }
)

MAX_QUEUE = 16000
MAX_BYTES = 1024 * 1024


def read_json(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def session_path(storage, root):
    key = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    return storage / ".biblioteca" / ("playback_" + key + ".json")


def playback_startup_root(root: Path, storage: Path | None, fixed_folder=False):
    """Desktop HTTP ports change, so the last library cannot live only in the browser."""
    if fixed_folder:
        return root
    storage = (storage or DATA).expanduser().resolve()
    pointer = read_json(storage / ".biblioteca/last_playback_library.json") or {}
    value = pointer.get("library_path")
    if not isinstance(value, str) or not Path(value).is_absolute():
        return root
    try:
        candidate = Path(value).resolve(strict=True)
        saved = read_json(session_path(storage, candidate)) or {}
        if (
            candidate.is_dir()
            and candidate != storage
            and candidate not in storage.parents
            and saved.get("schema") == 1
            and saved.get("library_path") == str(candidate)
            and saved.get("enabled") is True
            and isinstance(saved.get("session"), dict)
        ):
            return candidate
    except (OSError, ValueError):
        pass
    return root


def ids(value):
    if (
        not isinstance(value, list)
        or len(value) > MAX_QUEUE
        or any(not isinstance(item, str) or not 1 <= len(item) <= 64 or item.startswith("stage-") for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(_("Invalid request"))
    return value


def finite_number(value, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise ValueError(_("Invalid request"))
    return max(minimum, min(maximum, value))


class PlaybackSessions:
    def playback_settings(self):
        saved = read_json(session_path(self.storage, self.root)) or {}
        if saved.get("schema") != 1 or saved.get("library_path") != str(self.root):
            saved = {}
        return {
            "schema": 1,
            "library_path": str(self.root),
            "enabled": saved.get("enabled") is not False,
            "session": saved.get("session"),
            "checkpoint_at": saved.get("checkpoint_at", 0)
            if type(saved.get("checkpoint_at", 0)) is int and 0 <= saved.get("checkpoint_at", 0) < 2**53
            else 0,
        }

    def normalize_session(self, value, *, restoring=False):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(_("Invalid request"))
        queue, order = ids(value.get("queue")), ids(value.get("order"))
        track_id = value.get("track_id")
        if not isinstance(track_id, str) or track_id not in queue or set(queue) != set(order):
            raise ValueError(_("Invalid request"))
        if type(value.get("shuffle")) is not bool or type(value.get("muted")) is not bool:
            raise ValueError(_("Invalid request"))
        if value.get("repeat") not in ("off", "all", "one"):
            raise ValueError(_("Invalid request"))
        position = finite_number(value.get("position_seconds"), 0, 7 * 86400)
        volume = finite_number(value.get("volume"), 0, 1)
        queue_revision = value.get("queue_revision", 0)
        if type(queue_revision) is not int or not 0 <= queue_revision < 2**53:
            raise ValueError(_("Invalid request"))
        known = set(self.by_id)
        if restoring:
            # A removed or symbolic file must not prevent the remaining queue from opening.
            for item in order:
                if item not in known:
                    continue
                try:
                    readable_music(self.root, self.by_id[item]["arquivo"])
                except (ValueError, OSError):
                    known.discard(item)
        if track_id not in known:
            if not restoring:
                raise ValueError(_("Song not found"))
            after = order.index(track_id) + 1
            track_id = next((item for item in order[after:] + order[:after] if item in known), None)
            position = 0
        if track_id is None:
            return None
        readable_music(self.root, self.by_id[track_id]["arquivo"])
        duration = self.by_id[track_id].get("duracao_segundos", 0)
        return {
            "track_id": track_id,
            "position_seconds": min(position, max(0, duration)) if duration else position,
            "queue": [item for item in queue if item in known],
            "order": [item for item in order if item in known],
            "shuffle": value["shuffle"],
            "repeat": value["repeat"],
            "volume": volume,
            "muted": value["muted"],
            "queue_revision": queue_revision,
        }

    def playback_session(self):
        with self.lock:
            saved = self.playback_settings()
            ready = self.plan is not None
            try:
                session = (
                    self.normalize_session(saved["session"], restoring=True) if ready and saved["enabled"] else None
                )
            except (ValueError, OSError, TypeError):
                session = None
            return {
                "library_path": str(self.root),
                "enabled": saved["enabled"],
                "session": session,
                "ready": ready,
                "checkpoint_at": saved["checkpoint_at"],
            }

    def save_playback_session(self, data):
        with self.lock:
            if self.closed or data.get("library_path") != str(self.root):
                raise ValueError(_("The open library changed; reload before saving playback."))
            if set(data) not in (
                {"library_path", "checkpoint_at", "enabled"},
                {"library_path", "checkpoint_at", "session"},
                {"library_path", "checkpoint_at", "progress"},
            ):
                raise ValueError(_("Invalid request"))
            stamp = data.get("checkpoint_at")
            if type(stamp) is not int or not 0 < stamp < 2**53:
                raise ValueError(_("Invalid request"))
            saved = self.playback_settings()
            if stamp <= saved["checkpoint_at"]:
                return {"enabled": saved["enabled"]}
            saved["checkpoint_at"] = stamp
            if "enabled" in data:
                if type(data["enabled"]) is not bool:
                    raise ValueError(_("Invalid request"))
                saved["enabled"] = data["enabled"]
                if not saved["enabled"]:
                    saved["session"] = None
            elif saved["enabled"]:
                if "progress" in data:
                    progress = data["progress"]
                    if not isinstance(progress, dict) or set(progress) != {
                        "track_id",
                        "position_seconds",
                        "shuffle",
                        "repeat",
                        "volume",
                        "muted",
                        "queue_revision",
                    }:
                        raise ValueError(_("Invalid request"))
                    previous = saved["session"]
                    if (
                        not isinstance(previous, dict)
                        or previous.get("track_id") != progress["track_id"]
                        or previous.get("queue_revision", 0) != progress["queue_revision"]
                    ):
                        return {"enabled": True}
                    saved["session"] = self.normalize_session({**previous, **progress})
                else:
                    saved["session"] = self.normalize_session(data["session"])
            else:
                return {"enabled": False}
            path = session_path(self.storage, self.root)
            if path.is_symlink():
                raise ValueError(_("Invalid request"))
            atomic_json(path, saved)
            pointer = self.state_dir / "last_playback_library.json"
            if saved["enabled"] and saved["session"] is not None:
                if (read_json(pointer) or {}).get("library_path") != str(self.root):
                    atomic_json(pointer, {"library_path": str(self.root)})
            elif (read_json(pointer) or {}).get("library_path") == str(self.root):
                pointer.unlink(missing_ok=True)
            return {"enabled": saved["enabled"]}
