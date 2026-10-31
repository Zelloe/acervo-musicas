"""Bulk lyrics are looked up without writes, then saved in one reviewed transaction."""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1, USLT

from library_core import integrity, sha256
from library_plan import create_plan
from lyrics import current_lyrics
import protections
import server as server_module
from server import App, Handler


@unittest.skipUnless(shutil.which("ffmpeg"), "Tests generate temporary audio with ffmpeg")
class BulkLyricsHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.TemporaryDirectory()
        cls.sample_root = Path(cls.samples.name)
        for extension, codec in (("mp3", "libmp3lame"), ("flac", "flac")):
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=0.12", "-c:a", codec, str(cls.sample_root / ("sample." + extension))], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.samples.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "Collection"
        self.root.mkdir()
        for name, title in (("a.mp3", "First"), ("b.flac", "Second"), ("c.mp3", "Instrumental"), ("d.mp3", "Existing")):
            path = self.root / name
            shutil.copy(self.sample_root / ("sample" + path.suffix), path)
            if path.suffix == ".mp3":
                tags = ID3()
                for frame in (TIT2(encoding=3, text=[title]), TPE1(encoding=3, text=["Fixture band"]), TALB(encoding=3, text=["Fixture album"]), TCON(encoding=3, text=["Rock"])):
                    tags.add(frame)
                if title == "Existing":
                    tags.add(USLT(encoding=3, lang="XXX", desc="", text="[00:00.00]Existing timed verse"))
                tags.save(path, v2_version=3)
            else:
                audio = FLAC(path)
                audio["title"], audio["artist"], audio["album"], audio["genre"] = title, "Fixture band", "Fixture album", "Rock"
                audio.save()
        self.before = {str(path): sha256(path) for path in self.root.iterdir()}
        self.audio_hashes = {str(path): integrity(path)["audio_sha256"] for path in self.root.iterdir()}
        self.storage = base / "data"
        create_plan(self.root, self.storage / "relatorios/initial", storage=self.storage)
        self.app = App(self.root, self.storage)
        self.tracks = {track["titulo"][0]: track for track in self.app.tracks}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.provider = patch.object(self.app.lyrics_client, "lookup", side_effect=self.lookup).start()
        self.addCleanup(patch.stopall)

    def stop(self):
        if self.app.bulk_lyrics_cancel:
            self.app.bulk_lyrics_cancel.set()
        if self.app.worker:
            self.app.worker.join(5)
        self.server.shutdown()
        self.thread.join()
        self.app.global_search.close()

    @staticmethod
    def lookup(artist, title, album, duration):
        if title == "First":
            return {"encontrada": True, "sincronizada": [{"t": 0, "linha": "First <b>verse</b>"}, {"t": .08, "linha": "Last verse"}], "texto": "First <b>verse</b>\nLast verse"}
        if title == "Second":
            return {"encontrada": True, "sincronizada": [], "texto": "Plain fixture verse"}
        if title == "Instrumental":
            return {"encontrada": True, "instrumental": True}
        raise AssertionError("Existing synced lyrics must not be queried")

    def request(self, route, data=None, status=200, **extra):
        headers = {"Origin": self.url, "Content-Type": "application/json", "X-Library-Token": self.app.token, **extra}
        request = Request(self.url + route, None if data is None else json.dumps(data).encode(), headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result

    def wait_job(self, status="completed"):
        self.app.worker.join(5)
        self.assertFalse(self.app.worker.is_alive())
        self.assertEqual(self.app.job["status"], status, self.app.job)
        return self.request("/api/lyrics/bulk")["lookup"]

    def find(self):
        self.request("/api/lyrics/bulk/start", {}, status=202)
        return self.wait_job()

    def review(self, found=None):
        found = found or self.find()
        ids = [row["id"] for row in found["rows"] if row["status"] in ("synced", "plain")]
        return self.request("/api/lyrics/bulk/preview", {"lookup": found["id"], "ids": ids})

    def test_lookup_and_preview_do_not_write_or_change_signed_inventory(self):
        plan = self.app.plan_path.read_bytes()
        inventory = (self.app.plan_path.parent / "inventario.json").read_bytes()
        found = self.find()
        self.assertEqual({row["title"][0]: row["status"] for row in found["rows"]},
                         {"First": "synced", "Second": "plain", "Instrumental": "instrumental", "Existing": "already_synced"})
        self.assertEqual(self.provider.call_count, 3)
        preview = self.review(found)
        self.assertEqual(preview["count"], 2)
        self.assertEqual(preview["skipped"], 0)
        self.assertFalse((self.storage / "backups").exists())
        self.assertEqual({path: sha256(Path(path)) for path in self.before}, self.before)
        self.assertEqual(self.app.plan_path.read_bytes(), plan)
        self.assertEqual((self.app.plan_path.parent / "inventario.json").read_bytes(), inventory)

    def test_one_confirmation_saves_mp3_and_flac_with_one_backup_and_one_exact_undo(self):
        preview = self.review()
        self.request("/api/lyrics/bulk/save", {"preview": preview["preview"]}, status=202)
        self.wait_job()
        first, second = Path(self.tracks["First"]["arquivo"]), Path(self.tracks["Second"]["arquivo"])
        self.assertEqual(current_lyrics(first), ["[00:00.00]First <b>verse</b>\n[00:00.08]Last verse"])
        self.assertEqual(current_lyrics(second), ["Plain fixture verse"])
        self.assertEqual({path: integrity(Path(path))["audio_sha256"] for path in self.before}, self.audio_hashes)
        history = self.request("/api/history")["runs"]
        self.assertEqual(len(history), 1)
        self.assertEqual((history[0]["files"], history[0]["category"]), (2, "lyrics"))
        manifest = next((self.storage / "backups").glob("*/manifesto.json"))
        self.assertEqual(len(json.loads(manifest.read_text())["arquivos"]), 2)
        found = self.request("/api/lyrics/bulk")["lookup"]
        self.assertEqual(sum(bool(row.get("saved")) for row in found["rows"]), 2)
        self.request("/api/lyrics/bulk/save", {"preview": preview["preview"]}, status=400)
        self.request("/api/restore", {"id": history[0]["id"]}, status=202)
        self.wait_job()
        self.assertEqual({path: sha256(Path(path)) for path in self.before}, self.before)

    def test_changes_after_preview_abort_the_whole_batch_without_overwriting_any_track(self):
        preview = self.review()
        first = Path(self.tracks["First"]["arquivo"])
        tags = ID3(first)
        tags["TIT2"] = TIT2(encoding=3, text=["Later external title"])
        tags.save(first, v2_version=3)
        expected = {path: sha256(Path(path)) for path in self.before}
        self.request("/api/lyrics/bulk/save", {"preview": preview["preview"]}, status=202)
        self.wait_job("error")
        self.assertEqual({path: sha256(Path(path)) for path in self.before}, expected)
        self.assertFalse((self.storage / "backups").exists())

    def test_change_after_lookup_requires_reviewing_new_results(self):
        found = self.find()
        first = Path(self.tracks["First"]["arquivo"])
        tags = ID3(first)
        tags["TIT2"] = TIT2(encoding=3, text=["External change"])
        tags.save(first, v2_version=3)
        self.request("/api/lyrics/bulk/preview", {"lookup": found["id"], "ids": [self.tracks["First"]["id"]]}, status=400)
        self.assertFalse((self.storage / "backups").exists())

    def test_cancellation_keeps_results_without_writes_and_does_not_block_state_reads(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(5)
            return self.lookup(*args)
        self.provider.side_effect = slow
        self.request("/api/lyrics/bulk/start", {}, status=202)
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.app.lock.acquire(timeout=.3))
            self.app.lock.release()
            self.assertEqual(self.request("/api/state")["job"]["status"], "running")
            self.request("/api/lyrics/bulk/start", {}, status=400)
            self.request("/api/lyrics/bulk/cancel", {})
        finally:
            release.set()
        found = self.wait_job()
        self.assertTrue(found["cancelled"])
        self.assertTrue(any(row["status"] == "not_checked" for row in found["rows"]))
        self.assertEqual({path: sha256(Path(path)) for path in self.before}, self.before)
        self.assertFalse((self.storage / "backups").exists())

    def test_fake_ids_free_text_paths_and_unselected_statuses_are_refused(self):
        self.assertIsNone(self.request("/api/lyrics/bulk")["lookup"])
        for data in ({"files": ["/etc/passwd"]}, {"text": "arbitrary lyrics"}, {"ids": ["fake"]}):
            self.request("/api/lyrics/bulk/start", data, status=400)
        self.request("/api/lyrics/bulk/start", {}, status=403, **{"Origin": "http://evil.invalid"})
        self.request("/api/lyrics/bulk/start", {}, status=403, **{"X-Library-Token": "wrong"})
        self.provider.assert_not_called()
        found = self.find()
        good = self.tracks["First"]["id"]
        bad = self.tracks["Instrumental"]["id"]
        for data in ({"lookup": "fake", "ids": [good]}, {"lookup": found["id"], "ids": []}, {"lookup": found["id"], "ids": [good, good]}, {"lookup": found["id"], "ids": [bad]}, {"lookup": found["id"], "ids": ["fake"]}, {"lookup": found["id"], "ids": [good], "text": "injected"}):
            self.request("/api/lyrics/bulk/preview", data, status=400)
        self.request("/api/lyrics/bulk?id=fake", status=400)
        self.request("/api/lyrics/bulk?file=/etc/passwd", status=400)
        self.request("/api/lyrics/bulk/save", {"preview": "fake"}, status=400)
        self.assertFalse((self.storage / "backups").exists())

    def test_corrupt_policy_allows_lookup_but_refuses_saving(self):
        path = protections.policy_path(self.storage, self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("broken json")
        found = self.find()
        self.request("/api/lyrics/bulk/preview", {"lookup": found["id"], "ids": [self.tracks["First"]["id"]]}, status=400)
        self.assertFalse((self.storage / "backups").exists())

    def test_sidecar_sync_is_selected_and_embedded_in_the_tag_without_changing_the_sidecar(self):
        path = Path(self.tracks["First"]["arquivo"])
        sidecar = path.with_suffix(".lrc")
        text = "[00:00.00]Local first verse\n[00:00.08]Local last verse"
        sidecar.write_text(text)
        original_sidecar = sha256(sidecar)
        found = self.find()
        row = next(row for row in found["rows"] if row["id"] == self.tracks["First"]["id"])
        self.assertEqual((row["status"], row["current"], row["source"]), ("synced", "none", "lrc_file"))
        self.assertFalse(any(call.args[1] == "First" for call in self.provider.call_args_list))
        preview = self.request("/api/lyrics/bulk/preview", {"lookup": found["id"], "ids": [row["id"]]})
        self.request("/api/lyrics/bulk/save", {"preview": preview["preview"]}, status=202)
        self.wait_job()
        self.assertEqual(current_lyrics(path), [text])
        self.assertEqual(sha256(sidecar), original_sidecar)
        history = self.request("/api/history")["runs"]
        self.request("/api/restore", {"id": history[0]["id"]}, status=202)
        self.wait_job()
        self.assertEqual(sha256(path), self.before[str(path)])
        self.assertEqual(sha256(sidecar), original_sidecar)

    def test_large_preview_does_not_hold_the_app_lock_or_allow_competing_jobs(self):
        found = self.find()
        entered, release = threading.Event(), threading.Event()
        path = Path(self.tracks["First"]["arquivo"])
        original = server_module.sha256
        results = []
        def slow(file, *args):
            if Path(file) == path:
                entered.set()
                release.wait(5)
            return original(file, *args)
        with patch("server.sha256", side_effect=slow):
            worker = threading.Thread(target=lambda: results.append(self.request("/api/lyrics/bulk/preview", {"lookup": found["id"], "ids": [self.tracks["First"]["id"]]})))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.request("/api/state")["lyrics_busy"])
                self.request("/api/analyze", {}, status=400)
            finally:
                release.set()
                worker.join(5)
        self.assertEqual(len(results), 1)
        self.assertFalse(self.app.lyrics_busy)
        self.assertEqual({file: sha256(Path(file)) for file in self.before}, self.before)

    def test_save_is_a_writing_job_and_cannot_be_cancelled_as_a_lookup(self):
        preview = self.review()
        entered, release = threading.Event(), threading.Event()
        original = server_module.save_lyrics_batch
        def slow(*args):
            entered.set()
            release.wait(5)
            return original(*args)
        with patch("server.save_lyrics_batch", side_effect=slow):
            self.request("/api/lyrics/bulk/save", {"preview": preview["preview"]}, status=202)
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.app.writing())
                self.request("/api/lyrics/bulk/cancel", {}, status=400)
            finally:
                release.set()
            self.wait_job()


if __name__ == "__main__":
    unittest.main()
