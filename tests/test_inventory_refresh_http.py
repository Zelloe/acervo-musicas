"""An old inventory must not hide tags edited through another library root."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1

from acervo.library.core import sha256
from acervo.library.plan import create_plan
from acervo.library.scan import read_track
from acervo.web.server import App, Handler


@unittest.skipUnless(shutil.which("ffmpeg"), "Tests generate temporary audio with ffmpeg")
class InventoryRefreshHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.TemporaryDirectory()
        cls.sample_root = Path(cls.samples.name)
        for extension, codec in (("mp3", "libmp3lame"), ("flac", "flac")):
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=duration=0.12",
                    "-c:a",
                    codec,
                    str(cls.sample_root / ("sample." + extension)),
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
        self.root = base / "Music"
        self.album = self.root / "Album"
        self.album.mkdir(parents=True)
        self.storage = base / "data"
        self.mp3, self.flac = self.album / "first.mp3", self.album / "second.flac"
        self.unchanged = self.root / "unchanged.mp3"
        for path in (self.mp3, self.flac, self.unchanged):
            shutil.copy(self.sample_root / ("sample" + path.suffix), path)
            if path.suffix == ".mp3":
                tags = ID3()
                for frame in (
                    TIT2(encoding=3, text=[path.stem]),
                    TPE1(encoding=3, text=["Fixture band"]),
                    TALB(encoding=3, text=["Fixture album"]),
                ):
                    tags.add(frame)
                if path == self.unchanged:
                    tags.add(TCON(encoding=3, text=["Existing genre"]))
                tags.save(path, v2_version=3)
            else:
                audio = FLAC(path)
                audio["title"], audio["artist"], audio["album"] = path.stem, "Fixture band", "Fixture album"
                audio.save()
        self.rules = base / "rules.csv"
        self.rules.write_text("artista,album,genero,origem\nFixture band,,Suggested genre,fixture\n", encoding="utf-8")
        result = create_plan(self.root, self.storage / "relatorios/parent", genre_path=self.rules, storage=self.storage)
        self.plan_path = Path(result["plano"])
        self.inventory_path = self.plan_path.parent / "inventario.json"
        self.saved_inventory = self.inventory_path.read_bytes()
        self.saved_plan = self.plan_path.read_bytes()

    def open_app(self):
        app = App(self.root, self.storage)
        self.addCleanup(app.global_search.close)
        return app

    def http(self, app):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.app = app
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            thread.join()

        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_port}"

    @staticmethod
    def get(url, route):
        with urlopen(url + route, timeout=5) as response:
            return json.load(response)

    def update_genres(self):
        tags = ID3(self.mp3)
        tags.add(TCON(encoding=3, text=["Updated MP3 genre"]))
        tags.save(self.mp3, v2_version=3)
        audio = FLAC(self.flac)
        audio["genre"] = ["Updated FLAC genre"]
        audio.save()

    def assert_saved_analysis_untouched(self):
        self.assertEqual(self.inventory_path.read_bytes(), self.saved_inventory)
        self.assertEqual(self.plan_path.read_bytes(), self.saved_plan)
        self.assertFalse((self.storage / "backups").exists())

    def test_nested_root_edits_refresh_genres_and_withhold_old_proposals(self):
        self.update_genres()
        create_plan(self.album, self.storage / "relatorios/child", genre_path=self.rules, storage=self.storage)
        before = {path: sha256(path) for path in (self.mp3, self.flac, self.unchanged)}
        with patch("acervo.app.read_track", wraps=read_track) as reader:
            app = self.open_app()
        self.assertCountEqual([call.args[0] for call in reader.call_args_list], [self.mp3, self.flac])
        self.assertEqual(app.root, self.root)
        url = self.http(app)
        tracks = self.get(url, "/api/library")["tracks"]
        self.assertEqual(
            {row["title"][0]: row["genre"] for row in tracks},
            {"first": ["Updated MP3 genre"], "second": ["Updated FLAC genre"], "unchanged": ["Existing genre"]},
        )
        self.assertEqual(self.get(url, "/api/library?filter=missing-genre")["total"], 0)
        state = self.get(url, "/api/state")
        self.assertEqual(state["library"], str(self.root))
        self.assertEqual(state["summary"]["missing_fields"]["genre"], 0)
        self.assertEqual(self.get(url, "/api/proposals")["changes"], [])
        self.assertEqual({path: sha256(path) for path in before}, before)
        self.assert_saved_analysis_untouched()

    def test_unchanged_inventory_keeps_valid_proposals_without_decoding_audio(self):
        before = {path: sha256(path) for path in (self.mp3, self.flac, self.unchanged)}
        with patch(
            "acervo.app.read_track", side_effect=AssertionError("Unchanged audio should not be decoded")
        ) as reader:
            app = self.open_app()
        reader.assert_not_called()
        self.assertEqual(len(app.tracks), 3)
        self.assertEqual(len(app.proposals()), 2)
        self.assertEqual(app.snapshot()["resumo"]["campos_ausentes"]["genero"], 2)
        self.assertEqual({path: sha256(path) for path in before}, before)
        self.assert_saved_analysis_untouched()

    def test_deleted_track_is_not_kept_as_a_stale_missing_genre_row(self):
        self.mp3.unlink()
        app = self.open_app()
        url = self.http(app)
        self.assertEqual(self.get(url, "/api/library")["total"], 2)
        state = self.get(url, "/api/state")
        self.assertIn("Could not refresh track first.mp3", state["error"])
        self.assertEqual(state["summary"]["missing_fields"]["genre"], 1)
        self.assertEqual([row["arquivo"] for row in app.proposals()], [str(self.flac)])
        self.assert_saved_analysis_untouched()

    def test_replaced_symbolic_track_is_not_read_outside_the_library(self):
        external = self.root.parent / "external.mp3"
        shutil.copy(self.mp3, external)
        before = sha256(external)
        self.mp3.unlink()
        self.mp3.symlink_to(external)
        with patch("acervo.app.read_track", side_effect=AssertionError("A symlink must not be decoded")) as reader:
            app = self.open_app()
        reader.assert_not_called()
        self.assertEqual(len(app.tracks), 2)
        self.assertIn("Invalid or symbolic music path", app.error)
        self.assertNotIn(str(self.mp3), {row["arquivo"] for row in app.proposals()})
        self.assertEqual(sha256(external), before)
        self.assert_saved_analysis_untouched()

    def test_track_changing_during_refresh_is_left_for_a_new_scan(self):
        self.update_genres()

        def concurrent_read(path):
            row = read_track(path)
            if path == self.mp3:
                stat = path.stat()
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            return row

        with patch("acervo.app.read_track", side_effect=concurrent_read):
            app = self.open_app()
        self.assertEqual(len(app.tracks), 2)
        self.assertIn("The file changed while being read", app.error)
        self.assertNotIn(str(self.mp3), {row["arquivo"] for row in app.proposals()})
        self.assertEqual(app.snapshot()["resumo"]["campos_ausentes"]["genero"], 0)
        self.assert_saved_analysis_untouched()

    def test_download_moved_after_a_session_is_reported_once_and_forgotten(self):
        cache = self.open_app().download_cache
        moved = self.root / "moved away.mp3"
        cache.write_text(
            json.dumps({"biblioteca": str(self.root), "arquivos": [str(self.unchanged), str(moved)]}), encoding="utf-8"
        )
        app = self.open_app()
        self.assertIn("1 downloaded tracks were moved or removed", app.error)
        self.assertNotIn("Download saved", app.error)
        self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["arquivos"], [str(self.unchanged)])
        self.assertIn(str(self.unchanged), self.open_app().incremental_paths)
        self.assertIsNone(self.open_app().error)

    def test_startup_rereads_only_cached_tracks_that_need_it(self):
        cache = self.open_app().download_cache
        new = self.root / "new download.mp3"
        shutil.copy(self.sample_root / "sample.mp3", new)
        link = self.root / "link.mp3"
        link.symlink_to(self.unchanged)
        cache.write_text(
            json.dumps({"biblioteca": str(self.root), "arquivos": [str(self.unchanged), str(new), str(link)]}),
            encoding="utf-8",
        )
        with patch("acervo.app.read_track", wraps=read_track) as reader:
            app = self.open_app()
        read = {Path(call.args[0]) for call in reader.call_args_list}
        self.assertNotIn(self.unchanged, read)
        self.assertIn(new, read)
        self.assertNotIn(link, read)
        self.assertLessEqual({str(self.unchanged), str(new)}, app.incremental_paths)
        self.assertIn(str(new), {t["arquivo"] for t in app.tracks})
        self.assertIn("link.mp3", app.error)
        audio = ID3(self.unchanged)
        audio.delall("TIT2")
        audio.add(TIT2(encoding=3, text=["Edited title"]))
        audio.save(self.unchanged, v2_version=3)
        reopened = self.open_app()
        self.assertEqual(
            reopened.by_id[next(t["id"] for t in reopened.tracks if t["arquivo"] == str(self.unchanged))]["titulo"],
            ["Edited title"],
        )


if __name__ == "__main__":
    unittest.main()
