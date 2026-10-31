"""Exibição de letras por ID, sem gravar na coleção nem bloquear o estado da UI."""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mutagen.id3 import ID3, USLT

from library_core import sha256
from server import App, Handler


class LyricsHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "Música"
        self.root.mkdir()
        self.path = self.root / "Faixa.mp3"
        ID3().save(self.path)
        self.app = App(self.root, base / "dados")
        self.track = {"id": "fixture", "arquivo": str(self.path), "titulo": ["Faixa inventada"], "artista": ["Banda fictícia"],
                      "artista_album": [], "album": ["Disco fictício"], "duracao_segundos": 123.4,
                      "formato": "MP3", "campos_ausentes": [], "capas": []}
        self.app.tracks = [self.track]
        self.app.by_id = {"fixture": self.track}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.addCleanup(self.server.server_close)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.worker.join()
        self.app.busca_global.fechar()

    def get(self, params=None, status=200, host=None, route="/api/letra"):
        route += "?" + urlencode(params if params is not None else {"id": "fixture"})
        request = Request(self.url + route, headers={} if host is None else {"Host": host})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            data = json.loads(response.read())
            self.assertEqual(response.status, status, data)
            return data

    def test_embedded_and_sidecar_lyrics_have_priority_without_network_or_writes(self):
        tags = ID3(self.path)
        tags.add(USLT(encoding=3, lang="por", desc="", text="Verso fictício do arquivo"))
        tags.save(self.path)
        original = sha256(self.path)
        with patch.object(self.app.letras, "buscar", side_effect=AssertionError("Não consultar a rede quando a letra já existe")):
            result = self.get()
            self.assertEqual(result["fonte"], "arquivo")
            self.assertEqual(result["texto"], "Verso fictício do arquivo")
        self.assertEqual(sha256(self.path), original)
        ID3().save(self.path)
        sidecar = self.path.with_suffix(".lrc")
        sidecar.write_text("[00:01.00]Primeiro verso fictício\n[00:02.00]Segundo verso fictício\n")
        original = {p: sha256(p) for p in (self.path, sidecar)}
        with patch.object(self.app.letras, "buscar", side_effect=AssertionError("O sidecar já possui letra")):
            result = self.get()
            self.assertEqual(result["sincronizada"][1], {"t": 2.0, "linha": "Segundo verso fictício"})
        self.assertEqual({p: sha256(p) for p in original}, original)

    def test_lookup_uses_only_inventory_metadata_and_keeps_cache_outside_library(self):
        expected = {"encontrada": True, "texto": "Letra fictícia", "sincronizada": [], "instrumental": False, "fonte": "lrclib", "url": "https://lrclib.net/api/get/1"}
        before = sha256(self.path)
        with patch.object(self.app.letras, "buscar", return_value=expected) as provider:
            self.assertEqual(self.get(), expected)
            provider.assert_called_once_with("Banda fictícia", "Faixa inventada", "Disco fictício", 123.4)
        self.assertFalse(self.app.letras.cache_dir.is_relative_to(self.root))
        self.assertEqual(sha256(self.path), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_unknown_id_free_text_external_file_symlink_and_untrusted_host_are_refused(self):
        with patch.object(self.app.letras, "buscar") as provider:
            for params in ({}, {"id": "missing"}, {"id": "fixture", "titulo": "texto livre"}, {"id": "fixture", "arquivo": "/tmp/x.mp3"}):
                self.get(params, status=400)
            self.get(host="evil.invalid", status=403)
            outside = self.root.parent / "outside.mp3"
            ID3().save(outside)
            self.track["arquivo"] = str(outside)
            self.get(status=400)
            self.path.unlink()
            self.path.symlink_to(outside)
            self.track["arquivo"] = str(self.path)
            self.get(status=400)
            provider.assert_not_called()

    def test_not_found_instrumental_and_service_failure_are_reported_without_job(self):
        for result in ({"encontrada": False}, {"encontrada": True, "instrumental": True, "fonte": "lrclib"}):
            with patch.object(self.app.letras, "buscar", return_value=result):
                self.assertEqual(self.get(), result)
        with patch.object(self.app.letras, "buscar", side_effect=RuntimeError("Serviço temporariamente indisponível")):
            self.assertIn("temporariamente", self.get(status=503)["erro"])
        self.assertIsNone(self.app.job)

    def test_pending_lookup_does_not_hold_library_lock_or_block_state_polling(self):
        entered, release = threading.Event(), threading.Event()
        def slow_lookup(*args):
            entered.set()
            release.wait(5)
            return {"encontrada": False}
        results = []
        with patch.object(self.app.letras, "buscar", side_effect=slow_lookup):
            request = threading.Thread(target=lambda: results.append(self.get()))
            request.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.app.lock.acquire(timeout=.5))
                self.app.lock.release()
                self.assertEqual(self.get(route="/api/state")["resumo"]["lidos"], 1)
            finally:
                release.set()
                request.join(5)
        self.assertEqual(results, [{"encontrada": False}])


if __name__ == "__main__":
    unittest.main()
