"""Persistent preferences, known IDs and validation through the local API."""

from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mutagen.id3 import ID3, TALB, TIT2, TPE1

from library_core import library_lock, metadata_values, sha256
from library_plan import create_plan
import protections
from api_contract import to_api
from server import App, Handler


@unittest.skipUnless(shutil.which("ffmpeg"), "Tests generate temporary audio with ffmpeg")
class ProtectionHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.TemporaryDirectory()
        cls.sample = Path(cls.samples.name) / "sample.mp3"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1", "-c:a", "libmp3lame", str(cls.sample)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.samples.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.lib, self.storage = self.base / "Coleção", self.base / "app"
        self.lib.mkdir()
        for name, artist in (("a.mp3", "AC/DC"), ("b.mp3", "AC/DC"), ("c.mp3", "Nirvana")):
            path = self.lib / name
            shutil.copy(self.sample, path)
            tags = ID3()
            for frame in (TIT2(encoding=3, text=[name]), TPE1(encoding=3, text=[artist]), TALB(encoding=3, text=["Mesmo álbum"])):
                tags.add(frame)
            tags.save(path, v2_version=3)
        self.before = {str(p): sha256(p) for p in self.lib.iterdir()}
        create_plan(self.lib, self.storage / "relatorios/inicial", True, storage=self.storage)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.server.app = App(self.lib, self.storage)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.a = next(t for t in self.server.app.tracks if Path(t['arquivo']).name == "a.mp3")
        self.c = next(t for t in self.server.app.tracks if Path(t['arquivo']).name == "c.mp3")

    def stop_server(self):
        self.server.shutdown()
        self.thread.join()

    def request(self, route, data=None, status=200, **headers):
        defaults = {"Content-Type": "application/json", "Origin": self.url, "X-Library-Token": self.server.app.token}
        defaults.update(headers)
        request = Request(self.url + route, None if data is None else json.dumps(to_api(data)).encode(), defaults)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result

    def change(self, enabled=True, **kwargs):
        data = {"ids": [self.a["id"]], "campos": ["genero"], "proteger": enabled, **kwargs}
        return self.request("/api/protections", data)

    def wait_job(self, state):
        self.server.app.worker.join(timeout=5)
        self.assertEqual(self.server.app.job['status'], state, self.server.app.job)

    def test_protection_survives_restart_hides_old_proposals_and_blocks_old_selection(self):
        old = next(p for p in self.server.app.proposals() if p['arquivo'] == self.a['arquivo'] and p['campo'] == "genero")
        self.change()
        details = self.request("/api/track?id=" + self.a["id"])
        self.assertEqual(details['protected_fields'], ['genre'])
        rows = self.request("/api/proposals")['changes']
        self.assertNotIn(old["id"], {p["id"] for p in rows})
        self.assertNotIn(self.a["id"], {t["id"] for t in self.request("/api/unidentified")['tracks']})
        state = self.request("/api/state")
        self.assertEqual(state['summary']['proposed_changes'], len(rows))
        self.server.app = App(self.lib, self.storage)
        self.assertEqual(self.request("/api/track?id=" + self.a["id"])['protected_fields'], ['genre'])
        self.request("/api/apply", {"ids": [old["id"]]}, status=202)
        self.wait_job("error")
        self.assertIn("protegido", self.server.app.error)
        self.assertEqual({p: sha256(Path(p)) for p in self.before}, self.before)
        self.change(False)
        self.request("/api/apply", {"ids": [old["id"]]}, status=202)
        self.wait_job("completed")
        self.assertEqual(metadata_values(Path(self.a['arquivo']))['genero'], ["Hard Rock"])
        history = self.request("/api/history")['runs'][0]
        self.change()
        self.request("/api/restore", {"id": history["id"]}, status=202)
        self.wait_job("error")
        self.assertEqual(metadata_values(Path(self.a['arquivo']))['genero'], ["Hard Rock"])
        self.change(False)
        self.request("/api/restore", {"id": history["id"]}, status=202)
        self.wait_job("completed")
        self.assertEqual({p: sha256(Path(p)) for p in self.before}, self.before)

    def test_album_uses_exact_artist_and_supports_partial_unlock(self):
        self.request("/api/protections", {"album_id": self.a["id"], "campos": ["album", "genero"], "proteger": True})
        album = self.request("/api/protections?album_id=" + self.a["id"])["album"]
        self.assertEqual((album['tracks'], album['fields']['genre']), (2, 2))
        self.assertEqual(self.request("/api/track?id=" + self.c["id"])['protected_fields'], [])
        self.change(False)
        album = self.request("/api/protections?album_id=" + self.a["id"])["album"]
        self.assertEqual((album['fields']['genre'], album['fields']["album"]), (1, 2))
        self.request("/api/protections", {"album_id": self.a["id"], "campos": ["genero"], "proteger": False})
        listing = self.request("/api/protections")['protections']
        self.assertEqual(len(listing), 2)
        self.assertTrue(all(row['fields'] == ["album"] for row in listing))
        self.assertEqual({p: sha256(Path(p)) for p in self.before}, self.before)

    def test_only_known_ids_and_valid_fields_are_accepted_with_authentication(self):
        valid = {"ids": [self.a["id"]], "campos": ["genero"], "proteger": True}
        self.request("/api/protections", valid, status=403, **{"X-Library-Token": "errado"})
        self.request("/api/protections", valid, status=403, Origin="http://evil.invalid")
        for change in ({"ids": ["../fora"]}, {"arquivo": self.a['arquivo']}, {"campos": ["capa"]}, {"campos": [[]]}, {"proteger": "false"}, {"ids": []}, {"ids": [self.a["id"], self.a["id"]]}, {"album_id": self.a["id"]}):
            with self.subTest(change=change):
                self.request("/api/protections", {**valid, **change}, status=400)
        self.assertEqual(self.request("/api/protections")['protections'], [])
        self.assertEqual({p: sha256(Path(p)) for p in self.before}, self.before)

    def test_hard_linked_music_can_be_read_and_protected_without_editing_it(self):
        os.link(self.a['arquivo'], self.base / "seed.mp3")
        self.change()
        self.assertEqual(self.request("/api/track?id=" + self.a["id"])['protected_fields'], ['genre'])
        with urlopen(Request(self.url + "/api/audio?id=" + self.a["id"], headers={"Range": "bytes=0-9"})) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(len(response.read()), 10)
        self.assertEqual({p: sha256(Path(p)) for p in self.before}, self.before)

    def test_busy_job_and_external_lock_refuse_policy_changes(self):
        data = {"ids": [self.a["id"]], "campos": ["genero"], "proteger": True}
        self.server.app.job = {"status": "running", "type": "apply"}
        self.request("/api/protections", data, status=400)
        self.server.app.job = None
        with library_lock(self.lib):
            result = self.request("/api/protections", data, status=400)
        self.assertIn("ocupada", result['error'])
        self.assertFalse(self.server.app.policy_busy)
        self.assertEqual(self.request("/api/protections")['protections'], [])

    def test_corrupt_policy_blocks_updates_and_old_plan_without_touching_music(self):
        old = self.server.app.proposals()[0]
        path = protections.policy_path(self.storage, self.lib)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("corrompido")
        self.assertTrue(self.request("/api/protections")['protection_error'])
        state = self.request("/api/state")
        self.assertTrue(state['protection_error'])
        self.assertEqual(state['proposal_count'], 0)
        self.assertEqual(self.request("/api/proposals")['changes'], [])
        details = self.request("/api/track?id=" + self.a["id"])
        self.assertTrue(details['protection_error'])
        self.assertIsNone(details['protected_fields'])
        self.request("/api/protections", {"ids": [self.a["id"]], "campos": ["genero"], "proteger": True}, status=400)
        self.request("/api/apply", {"ids": [old["id"]]}, status=400)
        self.assertIsNone(self.server.app.job)
        self.assertEqual(path.read_text(), "corrompido")
        self.assertEqual({p: sha256(Path(p)) for p in self.before}, self.before)

    def test_state_is_readable_and_jobs_are_blocked_while_policy_is_saved(self):
        entered, release = threading.Event(), threading.Event()
        original = protections.protect
        def slow_save(*args):
            entered.set()
            release.wait(5)
            return original(*args)
        results = []
        with patch("server.protections.protect", side_effect=slow_save):
            request = threading.Thread(target=lambda: results.append(self.change()))
            request.start()
            try:
                self.assertTrue(entered.wait(2))
                self.request("/api/state")
                self.request("/api/analyze", {}, status=400)
                self.request("/api/playlists/preview", {"nome": "teste"}, status=400)
            finally:
                release.set()
                request.join()
        self.assertEqual(results[0]['files'], 1)
        self.assertFalse(self.server.app.policy_busy)


if __name__ == "__main__":
    unittest.main()
