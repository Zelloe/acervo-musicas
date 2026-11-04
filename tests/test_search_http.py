"""Ranked search, suggestions and precise entity selection through HTTP."""

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from api_contract import to_api
from server import App, Handler


class SearchHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.lib = base / "Música"
        self.lib.mkdir()
        self.app = App(self.lib, base / "dados")
        self.tracks = [
            self.track("1", "Coração", "Banda Árvore", "Mesmo álbum"),
            self.track("2", "Outra faixa", "Coração", "Outro álbum"),
            self.track("3", "Canção distante", "Outra banda", "Mesmo álbum"),
        ]
        self.app.tracks = self.tracks
        self.app.by_id = {t["id"]: t for t in self.tracks}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.addCleanup(self.server.server_close)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.provider = patch.object(
            self.app.global_search,
            "suggest",
            return_value={"termos": ["coracao ao vivo"], "artistas": [{"nome": "Coração"}]},
        ).start()
        self.addCleanup(patch.stopall)

    def stop(self):
        self.server.shutdown()
        self.worker.join()
        self.app.global_search.close()

    def track(self, ident, title, artist, album):
        path = self.lib / (ident + ".mp3")
        path.write_bytes(b"audio fixture untouched")
        return {
            "id": ident,
            "arquivo": str(path),
            "titulo": [title],
            "artista": [artist],
            "artista_album": [artist],
            "album": [album],
            "genero": ["Rock"],
            "ano": ["2026"],
            "faixa": [ident],
            "disco": [],
            "formato": "MP3",
            "duracao_segundos": 1,
            "bitrate_bps": 128000,
            "campos_ausentes": [],
            "capas": [],
            "marcadores_versao": [],
            "arquivo_estavel_durante_leitura": True,
        }

    def request(self, route, params=None, status=200, data=None, host=None):
        if params:
            route += "?" + urlencode(to_api(params))
        headers = {
            "Origin": self.url,
            "X-Acervo-Language": "pt-BR",
            "X-Library-Token": self.app.token,
            "Content-Type": "application/json",
        }
        if host:
            headers["Host"] = host
        request = Request(self.url + route, None if data is None else json.dumps(to_api(data)).encode(), headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result

    def test_ranked_results_accents_typos_and_pagination_work_without_network(self):
        results = self.request("/api/search", {"q": "coracao", "limit": 1})
        self.assertEqual(results["total"], 2)
        self.assertEqual(results["tracks"][0]["id"], "1")
        self.assertEqual(self.request("/api/search", {"q": "coracao", "limit": 1, "offset": 1})["tracks"][0]["id"], "2")
        self.assertEqual(self.request("/api/library", {"q": "coracao"})["total"], 2)
        self.assertEqual(self.request("/api/search", {"q": "coracao arvre"})["tracks"][0]["id"], "1")
        self.assertEqual(self.request("/api/search", {"q": "distante arvore"})["total"], 0)
        self.provider.assert_not_called()

    def test_sort_orders_and_queue_follow_the_same_filters(self):
        first, second, third = self.tracks
        first.update(ano=["1999-05-01"], duracao_segundos=300, assinatura_antes={"mtime_ns": 3})
        second.update(ano=[], duracao_segundos=100, assinatura_antes={"mtime_ns": 1})
        third.update(ano=["2005"], duracao_segundos=200, assinatura_antes={"mtime_ns": 2})

        def order(sort, direction="asc"):
            return [t["id"] for t in self.request("/api/search", {"sort": sort, "direction": direction})["tracks"]]

        self.assertEqual(order("default"), ["1", "2", "3"])
        self.assertEqual(order("title"), ["3", "1", "2"])
        self.assertEqual(order("title", "desc"), ["2", "1", "3"])
        self.assertEqual(order("year"), ["1", "3", "2"])
        self.assertEqual(order("year", "desc"), ["3", "1", "2"], "Tracks without a year stay last")
        self.assertEqual(order("duration", "desc"), ["1", "3", "2"])
        self.assertEqual(order("modified", "desc"), ["1", "3", "2"])
        queue = self.request("/api/queue", {"q": "coracao", "sort": "title"})
        self.assertEqual(queue["total"], 2)
        self.assertEqual([t["id"] for t in queue["tracks"]], ["1", "2"])
        self.assertEqual(set(queue["tracks"][0]), {"id", "title", "artist", "album", "duration_seconds"})
        for params in ({"sort": "file"}, {"sort": "title", "direction": "sideways"}):
            self.assertIn("error", self.request("/api/search", params, status=400))

    def test_album_sort_keeps_disc_and_track_order(self):
        one, two, three = self.tracks
        one.update(album=["Same"], artista_album=["Band"], faixa=["10/12"], disco=["1"])
        two.update(album=["Same"], artista_album=["Band"], faixa=["2/12"], disco=["2"])
        three.update(album=["Same"], artista_album=["Band"], faixa=["2/12"], disco=["1"])
        self.assertEqual([t["id"] for t in self.request("/api/search", {"sort": "album"})["tracks"]], ["3", "1", "2"])

    def test_local_suggestions_cache_index_and_rebuild_for_new_download(self):
        suggestions = self.request("/api/suggestions", {"q": "coracao"})
        self.assertEqual(suggestions["tracks"][0]["id"], "1")
        self.assertEqual(suggestions["artists"][0]["name"], "Coração")
        index = self.app.search_index
        self.request("/api/suggestions", {"q": "arvore"})
        self.assertIs(self.app.search_index, index)
        new = self.track("4", "Faixa nova", "Nova banda", "Álbum novo")
        with patch("server.read_track", return_value=new):
            self.app.refresh_downloaded([{"estado": "baixado", "arquivo": new["arquivo"]}])
        suggestions = self.request("/api/suggestions", {"q": "Faixa nova"})
        self.assertEqual(len(suggestions["tracks"]), 1)
        self.assertEqual(suggestions["tracks"][0]["title"], "Faixa nova")
        self.assertIsNot(self.app.search_index, index)
        self.provider.assert_not_called()

    def test_artist_album_and_playlist_filters_use_exact_entities(self):
        self.assertEqual(self.request("/api/search", {"artist": "banda arvore"})["total"], 1)
        album = self.request("/api/search", {"album_id": "1"})
        self.assertEqual([t["id"] for t in album["tracks"]], ["1"])
        suggestions = self.request("/api/suggestions", {"q": "Mesmo album"})
        self.assertEqual(len(suggestions["albums"]), 2)
        for selection in ({"album_id": "1"}, {"artista": "Banda Árvore"}):
            preview = self.request("/api/playlists/preview", data={"nome": "Seleção", **selection})
            self.assertEqual(preview["tracks"], 1)
        self.assertFalse((self.lib / "Playlists").exists())

    def test_global_suggestions_do_not_start_download_or_change_collection(self):
        before = {p.name: p.read_bytes() for p in self.lib.iterdir()}
        for route in ("/api/suggestions", "/api/search"):
            result = self.request(route, {"q": "coracao", "escopo": "global"})
            self.assertEqual(result["terms"], ["coracao ao vivo"])
        self.assertEqual(self.provider.call_count, 2)
        self.assertIsNone(self.app.job)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.lib.iterdir()})

    def test_queries_scope_host_and_entity_ids_are_validated(self):
        for route in ("/api/search", "/api/suggestions"):
            for params in ({"q": "x" * 201}, {"q": "a\nb"}, {"escopo": "invalido"}):
                self.request(route, params, status=400)
        self.request("/api/search", {"album_id": "invalido"}, status=400)
        self.request("/api/suggestions", {"q": "test", "escopo": "global"}, host="evil.invalid", status=403)
        self.assertEqual(self.request("/api/suggestions", {"q": "x", "scope": "global"}), {"terms": [], "artists": []})
        self.provider.assert_not_called()

    def test_global_failure_is_visible_and_local_search_remains_available(self):
        self.provider.side_effect = RuntimeError("Catálogo indisponível")
        self.assertIn(
            "Catálogo indisponível",
            self.request("/api/suggestions", {"q": "test", "scope": "global"}, status=503)["error"],
        )
        self.assertEqual(self.request("/api/search", {"q": "coracao"})["total"], 2)
        self.assertIsNone(self.app.error)
        self.assertIsNone(self.app.job)


if __name__ == "__main__":
    unittest.main()
