"""Exportação pelo navegador, somente em coleções temporárias."""

from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1, TRCK

from biblioteca_core import library_lock, sha256
from biblioteca_plano import create_plan
from playlists import validate_m3u8
from servidor import App, Handler


@unittest.skipUnless(shutil.which("ffmpeg"), "Os testes geram áudio temporário com ffmpeg")
class PlaylistHTTPTest(unittest.TestCase):
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
        self.lib = self.base / "Coleção"
        self.lib.mkdir()
        for index in range(1, 53):
            path = self.lib / f"Ação {index:02d}.mp3"
            shutil.copy(self.sample, path)
            tags = ID3()
            frames = [TIT2(encoding=3, text=[f"Canção {index}"]), TPE1(encoding=3, text=["Banda A"]), TALB(encoding=3, text=["Mesmo álbum"]), TRCK(encoding=3, text=[str(index)])]
            if index != 1:
                frames.append(TCON(encoding=3, text=["Rock"]))
            for frame in frames:
                tags.add(frame)
            tags.save(path, v2_version=3)
        other = self.lib / "Outro artista.mp3"
        shutil.copy(self.sample, other)
        tags = ID3()
        for frame in [TIT2(encoding=3, text=["Canção diferente"]), TPE1(encoding=3, text=["Banda B"]), TALB(encoding=3, text=["Mesmo álbum"])]:
            tags.add(frame)
        tags.save(other, v2_version=3)
        self.before = {str(p): sha256(p) for p in self.lib.glob("*.mp3")}
        storage = self.base / "app"
        create_plan(self.lib, storage / "relatorios/inicial", True)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.server.app = App(self.lib, storage)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.thread.join()

    def request(self, route, data=None, status=200, **headers):
        defaults = {"Content-Type": "application/json", "Origin": self.url, "X-Library-Token": self.server.app.token}
        defaults.update(headers)
        request = Request(self.url + route, None if data is None else json.dumps(data).encode(), defaults)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result

    def preview(self, **kwargs):
        return self.request("/api/playlists/preview", {"nome": "Minha seleção", **kwargs})

    def export(self, status=200, **kwargs):
        return self.request("/api/playlists/export", {"nome": "Minha seleção", **kwargs}, status=status)

    def test_preview_is_read_only_and_export_includes_all_matching_pages(self):
        preview = self.preview(q="Banda A")
        self.assertEqual(preview["faixas"], 52)
        self.assertFalse((self.lib / "Playlists").exists())
        self.assertEqual(len(self.request("/api/library?q=Banda+A")["musicas"]), 50)
        result = self.export(q="Banda A")
        self.assertEqual((result["faixas"], result["encontradas"], result["ausentes"]), (52, 52, []))
        content = Path(result["playlist"]).read_bytes()
        self.assertTrue(content.startswith(b"#EXTM3U\n"))
        self.assertNotIn(str(self.base).encode(), content)
        self.assertIn("../Ação 52.mp3\n", content.decode("utf-8"))
        self.assertLess(content.index(b"Can\xc3\xa7\xc3\xa3o 2\n"), content.index(b"Can\xc3\xa7\xc3\xa3o 10\n"))
        self.assertEqual({p: sha256(Path(p)) for p in self.before}, self.before)
        moved = self.base / "outro disco" / "Coleção"
        shutil.copytree(self.lib, moved)
        self.assertEqual(validate_m3u8(moved / "Playlists/Minha seleção.m3u8")["ausentes"], [])

    def test_filter_matches_library_and_album_keeps_artists_separate(self):
        result = self.preview(filter="sem-genero")
        library = self.request("/api/library?" + urlencode({"filter": "sem-genero"}))
        self.assertEqual(result["faixas"], library["total"])
        albums = self.request("/api/albums")["albuns"]
        for artist, expected in (("Banda A", 52), ("Banda B", 1)):
            album = next(a for a in albums if a["artista"] == artist)
            self.assertEqual(self.preview(album_id=album["track_id"])["faixas"], expected)
        self.request("/api/playlists/preview", {"nome": "Vazia", "q": "nenhuma correspondência"}, status=400)
        self.request("/api/playlists/preview", {"nome": "Vazia", "album_id": "inexistente"}, status=400)
        self.assertFalse((self.lib / "Playlists").exists())

    def test_overwrite_needs_explicit_choice_and_unchanged_preview(self):
        result = self.export()
        path = Path(result["playlist"])
        original = path.read_bytes()
        preview = self.preview(q="Banda B")
        self.assertTrue(preview["existe"])
        self.export(status=400, q="Banda B")
        self.export(status=400, q="Banda B", sobrescrever="false")
        self.export(status=400, q="Banda B", sobrescrever=True)
        self.assertEqual(path.read_bytes(), original)
        path.write_text("editada por outro programa\n")
        self.export(status=400, q="Banda B", sobrescrever=True, anterior_sha256=preview["anterior_sha256"])
        self.assertEqual(path.read_text(), "editada por outro programa\n")
        preview = self.preview(q="Banda B")
        result = self.export(q="Banda B", sobrescrever=True, anterior_sha256=preview["anterior_sha256"])
        self.assertEqual(result["faixas"], 1)
        self.assertIn("Canção diferente", path.read_text())
        backup = Path(result["backup"])
        self.assertFalse(backup.is_relative_to(self.lib))
        self.assertIn("Minha seleção", backup.name)
        self.assertEqual(backup.read_text(), "editada por outro programa\n")
        preview = self.preview()
        original = path.read_bytes()
        with patch("servidor.durable_copy", side_effect=OSError("disco cheio")):
            self.export(status=400, sobrescrever=True, anterior_sha256=preview["anterior_sha256"])
        self.assertEqual(path.read_bytes(), original)

    def test_hard_linked_music_is_readable_and_state_polling_stays_available(self):
        os.link(self.lib / "Ação 01.mp3", self.base / "seed.mp3")
        self.assertEqual(self.preview()["faixas"], 53)
        entered, release = threading.Event(), threading.Event()
        from servidor import render_m3u8
        def slow_render(*args):
            entered.set()
            release.wait(5)
            return render_m3u8(*args)
        result = []
        with patch("servidor.render_m3u8", side_effect=slow_render):
            request = threading.Thread(target=lambda: result.append(self.preview()))
            request.start()
            try:
                self.assertTrue(entered.wait(2))
                start = time.monotonic()
                self.request("/api/state")
                self.assertLess(time.monotonic() - start, 1)
                self.request("/api/analyze", {}, status=400)
            finally:
                release.set()
                request.join()
        self.assertEqual(result[0]["faixas"], 53)
        self.assertFalse(self.server.app.playlist_busy)

    def test_names_and_links_cannot_write_outside_playlist_directory(self):
        for name in ("", "..", "../fora", "/tmp/fora", "a\\b", "a\nb", "a:b", [], "á" * 121):
            with self.subTest(name=name):
                self.request("/api/playlists/export", {"nome": name}, status=400)
        self.assertFalse((self.lib / "Playlists").exists())
        outside = self.base / "fora"
        outside.mkdir()
        folder = self.lib / "Playlists"
        folder.symlink_to(outside, target_is_directory=True)
        self.export(status=400)
        self.assertEqual(list(outside.iterdir()), [])
        folder.unlink()
        folder.mkdir()
        path = folder / "Minha seleção.m3u8"
        music = self.lib / "Ação 01.mp3"
        path.symlink_to(music)
        self.export(status=400, sobrescrever=True)
        path.unlink()
        os.link(music, path)
        self.export(status=400, sobrescrever=True)
        self.assertEqual(sha256(music), self.before[str(music)])

    def test_authentication_busy_job_and_missing_files_prevent_export(self):
        self.request("/api/playlists/export", {"nome": "Inválida"}, status=403, **{"X-Library-Token": "errado"})
        self.request("/api/playlists/export", {"nome": "Inválida"}, status=403, Origin="http://evil.invalid")
        self.request("/api/playlists/export", {"nome": "Inválida"}, status=403, Host="evil.invalid")
        self.server.app.job = {"estado": "executando", "tipo": "importacao"}
        self.export(status=400)
        self.server.app.job = None
        with library_lock(self.lib):
            result = self.export(status=400)
        self.assertIn("ocupada", result["erro"])
        self.assertFalse(self.server.app.playlist_busy)
        (self.lib / "Ação 01.mp3").unlink()
        self.export(status=400)
        self.assertFalse((self.lib / "Playlists").exists())


if __name__ == "__main__":
    unittest.main()
