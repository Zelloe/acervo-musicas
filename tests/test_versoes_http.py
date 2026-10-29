"""Tipos de gravação na biblioteca, playlists e metadados do catálogo remoto."""

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

from servidor import App, Handler
from versoes import classificar


class VersionHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "Coleção"
        self.root.mkdir()
        self.app = App(self.root, base / "dados")
        self.rows = [self.track("1", "To Live Is to Die", "...And Justice for All"),
                     self.track("2", "Carry On", "Holy Live"),
                     self.track("3", "Rebirth (Acoustic Version)", "Hunters and Prey"),
                     self.track("4", "Battery (Demo)", "Demo tapes"),
                     self.track("5", "Time - Remix", "Mixes"),
                     self.track("6", "Carry On", "The Best Of Angra"),
                     self.track("7", "Rebirth - Live", "Acoustic - Live At Ópera De Arame")]
        self.app.tracks = self.rows
        self.app.by_id = {t["id"]: t for t in self.rows}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.thread.join()
        self.app.busca_global.fechar()

    def track(self, ident, title, album):
        path = self.root / (ident + ".mp3")
        path.write_bytes(b"temporary fixture")
        return {"id": ident, "arquivo": str(path), "titulo": [title], "artista": ["Banda"], "artista_album": [],
                "album": [album], "genero": [], "ano": [], "faixa": [ident], "disco": [], "formato": "MP3",
                "duracao_segundos": 1, "bitrate_bps": 128000, "campos_ausentes": ["genero"], "capas": [],
                "marcadores_versao": ["live"] if "Live" in title else []}

    def request(self, route, params=None, data=None, status=200):
        if params:
            route += "?" + urlencode(params)
        headers = {"Origin": self.url, "X-Library-Token": self.app.token, "Content-Type": "application/json"}
        request = Request(self.url + route, None if data is None else json.dumps(data).encode(), headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result

    def test_version_filters_use_qualifiers_and_keep_live_in_a_studio_title(self):
        expected = {"estudio": {"1"}, "ao_vivo": {"2", "7"}, "acustico": {"3", "7"}, "demo-remix": {"4", "5"}, "coletanea": {"6"}}
        before = [dict(t) for t in self.rows]
        for choice, ids in expected.items():
            with self.subTest(choice=choice):
                result = self.request("/api/busca", {"versao": choice})
                self.assertEqual({t["id"] for t in result["musicas"]}, ids)
                self.assertTrue(all(t["versao"]["rotulos"] for t in result["musicas"]))
        self.assertEqual(self.request("/api/library", {"versao": "todas"})["total"], 7)
        extras = self.request("/api/library", {"filter": "versoes"})
        self.assertNotIn("1", {t["id"] for t in extras["musicas"]})
        self.assertIn("2", {t["id"] for t in extras["musicas"]})
        self.assertEqual(self.app.tracks, before)

    def test_playlist_version_filter_includes_all_pages_and_same_paths_as_search(self):
        more = [self.track(str(i), "Carry On", "Holy Live") for i in range(8, 61)]
        self.app.tracks = self.rows + more
        self.app.by_id = {t["id"]: t for t in self.app.tracks}
        first = self.request("/api/busca", {"versao": "ao_vivo", "limit": 50})
        second = self.request("/api/busca", {"versao": "ao_vivo", "offset": 50})
        selected = first["musicas"] + second["musicas"]
        self.assertEqual(len(selected), 55)
        preview = self.request("/api/playlists/preview", data={"nome": "Ao vivo", "versao": "ao_vivo"})
        self.assertEqual(preview["faixas"], len(selected))
        self.assertFalse((self.root / "Playlists").exists())
        result = self.request("/api/playlists/export", data={"nome": "Ao vivo", "versao": "ao_vivo"})
        paths = {line for line in Path(result["playlist"]).read_text().splitlines() if line and not line.startswith("#")}
        self.assertEqual(paths, {"../" + t["id"] + ".mp3" for t in selected})

    def test_invalid_version_is_rejected_and_image_hosts_are_restricted(self):
        self.request("/api/busca", {"versao": "invalido"}, status=400)
        self.request("/api/playlists/preview", data={"nome": "Teste", "versao": "invalido"}, status=400)
        with urlopen(self.url) as response:
            policy = response.headers["Content-Security-Policy"]
            self.assertIn("https://i.scdn.co", policy)
            self.assertIn("https://*.googleusercontent.com", policy)
            self.assertIn("https://*.spotifycdn.com", policy)
            self.assertIn("connect-src 'self'", policy)
            self.assertNotIn("img-src *", policy)
            self.assertIn(b'/versoes.js', response.read())
        with urlopen(self.url + "/versoes.js") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("javascript", response.headers["Content-Type"])

    def test_download_catalog_keeps_photos_and_classification_without_downloading(self):
        song = {"titulo": "Carry On", "artista": "Angra", "album": "Holy Live", "capa": "https://i.scdn.co/image/fixture",
                "versao": classificar("Carry On", "Holy Live"), "url": "https://open.spotify.com/track/Fixture"}
        with patch("seletor_downloads.baixador.buscar_musicas", return_value=[song]), patch("seletor_downloads.baixador.baixar") as download:
            self.request("/api/baixar/buscar", data={"tipo": "musica", "consulta": "Angra"}, status=202)
            self.app.worker.join(5)
            result = self.request("/api/baixar")["catalogo"]["itens"][0]
            self.assertEqual(result["capa"], song["capa"])
            self.assertEqual(result["versao"]["tipo"], "ao_vivo")
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
