"""Playback checkpoints survive a desktop port change without touching audio."""

import copy
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mutagen.id3 import ID3, TCON, TIT2

from acervo.app import App
from acervo.app.session import MAX_QUEUE, playback_startup_root, session_path
from acervo.library.core import sha256
from acervo.library.plan import create_plan
from acervo.web.server import Handler


@unittest.skipUnless(shutil.which("ffmpeg"), "Temporary audio requires ffmpeg")
class PlaybackSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.TemporaryDirectory()
        cls.sample = Path(cls.samples.name) / "sample.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=2",
                "-c:a",
                "libmp3lame",
                str(cls.sample),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.samples.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.storage = base / "Music", base / "data"
        self.root.mkdir()
        for index in range(3):
            path = self.root / f"{index}.mp3"
            shutil.copy(self.sample, path)
            if index == 0:
                tags = ID3(path)
                tags.add(TIT2(encoding=3, text=["A tagged track"]))
                tags.add(TCON(encoding=3, text=["Rock"]))
                tags.save(path)
            else:
                ID3(path).delete(path)
        create_plan(self.root, self.storage / "relatorios/initial", storage=self.storage)
        self.before = {path: sha256(path) for path in self.root.glob("*.mp3")}
        self.app = self.open_app()
        self.url = self.http(self.app)
        self.ids = [row["id"] for row in self.app.tracks]
        self.session = {
            "track_id": self.ids[1],
            "position_seconds": 1.25,
            "queue": self.ids,
            "order": [self.ids[2], self.ids[1], self.ids[0]],
            "shuffle": True,
            "repeat": "all",
            "volume": 0.35,
            "muted": True,
            "queue_revision": 1,
        }

    def tearDown(self):
        for path, digest in self.before.items():
            if path.is_file() and not path.is_symlink():
                self.assertEqual(sha256(path), digest)
        self.assertFalse((self.storage / "backups").exists())

    def open_app(self, root=None):
        app = App(root or self.root, self.storage)
        self.addCleanup(app.global_search.close)
        return app

    def http(self, app):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.app = app
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: server.app.global_search.close())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            thread.join()

        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_port}"

    def get(self, route="/api/playback/session", url=None):
        with urlopen((url or self.url) + route, timeout=5) as response:
            return json.load(response)

    def post(self, fields, stamp=1, *, root=None, token=None):
        body = {"library_path": str(root or self.root), "checkpoint_at": stamp, **fields}
        request = Request(
            self.url + "/api/playback/session",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Origin": self.url,
                "Content-Type": "application/json",
                "X-Library-Token": token or self.app.token,
            },
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_reopen_on_new_port_retains_full_session_and_last_library(self):
        self.assertTrue(self.get()["enabled"])
        self.assertIsNone(self.get()["session"])
        self.assertEqual(self.post({"session": self.session})[0], 200)
        reopened = self.open_app()
        other_url = self.http(reopened)
        self.assertNotEqual(other_url, self.url)
        self.assertEqual(self.get(url=other_url)["session"], self.session)
        default = self.root.parent / "Default"
        default.mkdir()
        self.assertEqual(playback_startup_root(default, self.storage), self.root)
        self.assertEqual(playback_startup_root(default, self.storage, fixed_folder=True), default)
        self.post({"session": {**self.session, "position_seconds": 0.5}}, stamp=2)
        self.assertEqual(playback_startup_root(default, self.storage), self.root)

    def test_disabling_forgets_session_and_survives_restart(self):
        self.post({"session": self.session})
        self.assertEqual(self.post({"enabled": False}, stamp=2), (200, {"enabled": False}))
        self.assertFalse(self.get()["enabled"])
        self.assertIsNone(self.get()["session"])
        self.assertEqual(self.post({"session": self.session}, stamp=3), (200, {"enabled": False}))
        reopened = self.open_app()
        self.assertFalse(reopened.playback_session()["enabled"])
        self.assertIsNone(reopened.playback_session()["session"])
        self.assertFalse((self.app.state_dir / "last_playback_library.json").exists())
        self.assertEqual(self.post({"enabled": True}, stamp=4)[0], 200)
        self.assertIsNone(self.get()["session"])

    def test_old_inflight_checkpoint_cannot_overwrite_close_or_preferences(self):
        self.post({"session": self.session}, stamp=20)
        old = {**self.session, "position_seconds": 0}
        self.post({"session": old}, stamp=19)
        self.assertEqual(self.get()["session"], self.session)
        self.post({"enabled": False}, stamp=21)
        self.post({"session": self.session}, stamp=20)
        self.assertFalse(self.get()["enabled"])
        self.assertIsNone(self.get()["session"])

    def test_compact_closing_checkpoint_cannot_erase_a_new_queue(self):
        self.post({"session": self.session}, stamp=20)
        progress = {key: value for key, value in self.session.items() if key not in ("queue", "order")}
        progress["position_seconds"] = 1.5
        self.assertEqual(self.post({"progress": progress}, stamp=30)[0], 200)
        self.assertEqual(self.get()["session"]["position_seconds"], 1.5)
        self.post({"session": self.session}, stamp=25)
        self.assertEqual(self.get()["session"]["position_seconds"], 1.5)
        # A compact update arriving before its new full queue is ignored without advancing the stamp.
        progress["queue_revision"] = 2
        self.post({"progress": progress}, stamp=50)
        revised = {**self.session, "order": self.ids, "queue_revision": 2}
        self.post({"session": revised}, stamp=40)
        self.assertEqual(self.get()["session"]["order"], self.ids)
        self.post({"progress": progress}, stamp=51)
        self.assertEqual(self.get()["session"]["position_seconds"], 1.5)

    def test_checkpoints_cannot_cross_library_or_authentication_boundary(self):
        self.assertEqual(self.post({"session": self.session}, token="wrong")[0], 403)
        other = self.root.parent / "Other"
        other.mkdir()
        self.assertEqual(self.post({"session": self.session}, root=other)[0], 400)
        self.assertIsNone(self.get()["session"])
        self.app.closed = True
        self.assertEqual(self.post({"session": self.session})[0], 400)

    def test_switching_libraries_keeps_separate_sessions_and_rejects_late_old_writes(self):
        self.post({"session": self.session})
        other = self.root.parent / "Other"
        other.mkdir()
        shutil.copy(self.sample, other / "new.mp3")
        create_plan(other, self.storage / "relatorios/other", storage=self.storage)
        request = Request(
            self.url + "/api/library/open",
            data=json.dumps({"folder": str(other)}).encode("utf-8"),
            headers={"Origin": self.url, "Content-Type": "application/json", "X-Library-Token": self.app.token},
        )
        with urlopen(request, timeout=5) as response:
            self.assertEqual(json.load(response)["library"], str(other))
        self.assertTrue(self.app.closed)
        self.assertEqual(self.get()["library_path"], str(other))
        self.assertIsNone(self.get()["session"])
        self.assertEqual(self.post({"session": self.session}, stamp=2)[0], 400)
        new_id = self.get("/api/queue")["tracks"][0]["id"]
        new_session = {**self.session, "track_id": new_id, "queue": [new_id], "order": [new_id]}
        self.assertEqual(self.post({"session": new_session}, root=other, stamp=3)[0], 200)
        self.assertEqual(self.get()["session"], new_session)
        self.assertEqual(self.app.playback_session()["session"], self.session)
        self.assertEqual(playback_startup_root(self.root, self.storage), other)

    def test_deleted_current_track_falls_forward_in_saved_order(self):
        self.post({"session": self.session})
        Path(self.app.by_id[self.ids[1]]["arquivo"]).unlink()
        session = self.get()["session"]
        self.assertEqual(session["track_id"], self.ids[0])
        self.assertEqual(session["position_seconds"], 0)
        self.assertEqual(session["queue"], [self.ids[0], self.ids[2]])
        self.assertEqual(session["order"], [self.ids[2], self.ids[0]])
        for track in self.app.tracks:
            Path(track["arquivo"]).unlink(missing_ok=True)
        self.assertIsNone(self.get()["session"])

    def test_initial_scan_does_not_erase_checkpoint(self):
        self.post({"session": self.session})
        path = session_path(self.storage, self.root)
        before = path.read_bytes()
        plan, self.app.plan = self.app.plan, None
        self.assertFalse(self.get()["ready"])
        self.assertIsNone(self.get()["session"])
        self.assertEqual(path.read_bytes(), before)
        self.app.plan = plan
        self.assertEqual(self.get()["session"], self.session)

    def test_invalid_checkpoint_is_rejected_without_losing_previous_one(self):
        self.post({"session": self.session})
        invalid = [
            {"position_seconds": float("nan")},
            {"position_seconds": True},
            {"volume": float("inf")},
            {"shuffle": "yes"},
            {"muted": 0},
            {"repeat": "forever"},
            {"track_id": "unknown"},
            {"queue": [self.ids[0]]},
            {"queue": self.ids + [self.ids[0]]},
            {"order": self.ids + ["stage-preview"]},
            {"queue": [str(i) for i in range(MAX_QUEUE + 1)]},
        ]
        for changes in invalid:
            with self.subTest(changes=list(changes)):
                value = copy.deepcopy(self.session)
                value.update(changes)
                self.assertEqual(self.post({"session": value}, stamp=2)[0], 400)
                self.assertEqual(self.get()["session"], self.session)
        self.assertEqual(self.post({"enabled": "false"}, stamp=2)[0], 400)
        self.assertEqual(self.post({"session": self.session}, stamp=True)[0], 400)

    def test_out_of_range_position_and_volume_are_clamped(self):
        self.post({"session": {**self.session, "position_seconds": 1000, "volume": -4}})
        session = self.get()["session"]
        self.assertEqual(session["position_seconds"], self.app.by_id[self.ids[1]]["duracao_segundos"])
        self.assertEqual(session["volume"], 0)

    def test_corrupt_or_symbolic_state_does_not_prevent_opening_the_library(self):
        path = session_path(self.storage, self.root)
        for content in ("{bad", "[]", json.dumps({"schema": 999, "session": self.session}), "x" * (1024 * 1024 + 1)):
            path.write_text(content, encoding="utf-8")
            self.assertIsNone(self.get()["session"])
        external = self.root.parent / "external.json"
        external.write_text("private", encoding="utf-8")
        path.unlink()
        path.symlink_to(external)
        self.assertIsNone(self.get()["session"])
        self.assertEqual(self.post({"session": self.session})[0], 400)
        self.assertEqual(external.read_text(encoding="utf-8"), "private")

    def test_totals_include_tracks_without_any_tags_or_genre(self):
        state = self.get("/api/state")
        library = self.get("/api/library")
        queue = self.get("/api/queue")
        self.assertEqual(state["summary"]["read_count"], 3)
        self.assertEqual(state["summary"]["formats"], {"MP3": 3})
        self.assertEqual(state["summary"]["read_count"], library["total"])
        self.assertEqual(library["total"], queue["total"])
        self.assertEqual(self.get("/api/library?filter=missing-genre")["total"], 2)
        self.assertEqual(state["summary"]["missing_fields"]["genre"], 2)
        self.assertEqual(len(library["tracks"]), 3)


if __name__ == "__main__":
    unittest.main()
