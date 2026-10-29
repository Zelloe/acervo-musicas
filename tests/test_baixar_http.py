"""Seleção no navegador sem enviar comandos ou faixas arbitrárias ao spotDL."""

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

from mutagen.id3 import ID3, TALB, TIT2, TPE1

from biblioteca_core import sha256
from biblioteca_plano import create_plan
from servidor import App, Handler


@unittest.skipUnless(shutil.which("ffmpeg"), "Os testes geram áudio temporário com ffmpeg")
class SimpleDownloadHTTPTest(unittest.TestCase):
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
        self.music = self.lib / "Banda Teste - Existente.mp3"
        shutil.copy(self.sample, self.music)
        tags = ID3()
        for frame in (TIT2(encoding=3, text=["Existente"]), TPE1(encoding=3, text=["Banda Teste"]), TALB(encoding=3, text=["Álbum"])):
            tags.add(frame)
        tags.save(self.music, v2_version=3)
        self.before = sha256(self.music)
        storage = self.base / "app"
        create_plan(self.lib, storage / "relatorios/inicial", storage=storage)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.server.app = App(self.lib, storage)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.songs = [{"titulo": title, "artista": "Banda Teste", "album": "Álbum", "numero": index, "ano": "2026", "duracao": 100, "url": f"https://open.spotify.com/track/Faixa{index}"} for index, title in enumerate(("Existente", "Nova"), 1)]
        self.mock_songs = patch("seletor_downloads.baixador.buscar_musicas", return_value=self.songs).start()
        self.mock_tracks = patch("seletor_downloads.baixador.faixas", return_value=self.songs).start()
        self.mock_albums = patch("seletor_downloads.baixador.buscar_albuns", return_value=[{"titulo": "Álbum", "artista": "Banda Teste", "ano": "2026"}]).start()
        self.mock_editions = patch("seletor_downloads.baixador.edicoes", return_value=[{"titulo": "Álbum (Deluxe)", "url": "https://open.spotify.com/album/AlbumTeste"}, {"titulo": "Álbum", "url": "https://open.spotify.com/album/AlbumOriginal"}]).start()
        self.mock_download = patch("seletor_downloads.baixador.baixar", return_value=[]).start()
        self.addCleanup(patch.stopall)

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

    def wait_job(self, state="concluido"):
        self.server.app.worker.join(timeout=5)
        self.assertEqual(self.server.app.job["estado"], state, self.server.app.job)
        return self.request("/api/baixar")["catalogo"]

    def search(self, kind="musica", text="Banda Teste"):
        self.request("/api/baixar/buscar", {"tipo": kind, "consulta": text, "pasta": str(self.lib)}, status=202)
        return self.wait_job()

    def test_artist_album_edition_tracks_and_cached_back_navigation(self):
        self.assertIsNone(self.request("/api/baixar")["catalogo"])
        albums = self.search("artista")
        self.assertEqual(albums["tipo"], "albuns")
        self.request("/api/baixar/abrir", {"catalogo": albums["id"], "item": albums["itens"][0]["id"]}, status=202)
        editions = self.wait_job()
        self.mock_editions.assert_called_once_with("Banda Teste", "Álbum")
        self.request("/api/baixar/abrir", {"catalogo": editions["id"], "item": editions["itens"][0]["id"]}, status=202)
        tracks = self.wait_job()
        self.assertEqual(tracks["tipo"], "faixas")
        self.assertEqual([row["ja_tenho"] for row in tracks["itens"]], [True, False])
        self.assertEqual(self.request("/api/baixar?catalogo=" + albums["id"])["catalogo"], albums)
        self.assertNotIn("id", self.songs[0])
        self.assertEqual(sha256(self.music), self.before)

    def test_only_selected_server_rows_reach_downloader_and_chosen_destination(self):
        catalog = self.search()
        selected = catalog["itens"][1]
        destination = self.base / "Outra pasta"
        destination.mkdir()
        self.mock_download.return_value = [{**selected, "estado": "baixado", "arquivo": str(destination / "nova.mp3")}]
        self.request("/api/baixar/baixar", {"catalogo": catalog["id"], "ids": [selected["id"]], "pasta": str(destination), "bitrate": "256k"}, status=202)
        self.wait_job()
        args, kwargs = self.mock_download.call_args
        self.assertEqual(args, ([selected], destination))
        self.assertEqual(kwargs["bitrate"], "256k")
        self.assertTrue(callable(kwargs["progresso"]))
        result = self.request("/api/baixar")["resultado"]
        self.assertEqual((len(result), result[0]["titulo"], result[0]["estado"]), (1, "Nova", "baixado"))
        self.assertEqual(sha256(self.music), self.before)

    def test_single_edition_opens_tracks_without_an_extra_selection(self):
        self.mock_editions.return_value = [{"titulo": "Álbum", "url": "https://open.spotify.com/album/AlbumOriginal"}]
        albums = self.search("artista")
        self.request("/api/baixar/abrir", {"catalogo": albums["id"], "item": albums["itens"][0]["id"]}, status=202)
        self.assertEqual(self.wait_job()["tipo"], "faixas")
        self.mock_tracks.assert_called_once_with("https://open.spotify.com/album/AlbumOriginal")

    def test_new_downloads_update_only_their_inventory_and_survive_restart(self):
        from diagnostico_biblioteca import read_track
        app = self.server.app
        plan_path = app.plan_path
        signed_plan = plan_path.read_bytes()
        new_path = self.lib / "nova.mp3"
        def fake_download(rows, folder, **kwargs):
            shutil.copy(self.sample, new_path)
            tags = ID3()
            for frame in (TIT2(encoding=3, text=["Nova"]), TPE1(encoding=3, text=["Banda Teste"]), TALB(encoding=3, text=["Álbum"])):
                tags.add(frame)
            tags.save(new_path, v2_version=3)
            return [{**rows[0], "estado": "baixado", "arquivo": str(new_path)}]
        self.mock_download.side_effect = fake_download
        with patch("seletor_downloads.baixador.indice_local", side_effect=AssertionError("A coleção já está no inventário")), patch("servidor.create_plan", side_effect=AssertionError("Não reanalisar a coleção no download")), patch("servidor.read_track", wraps=read_track) as reader:
            catalog = self.search()
            self.request("/api/baixar/baixar", {"catalogo": catalog["id"], "ids": [catalog["itens"][1]["id"]]}, status=202)
            current = self.wait_job()
            self.assertEqual(reader.call_args_list[0].args, (new_path,))
            self.assertEqual(reader.call_count, 1)
            self.assertTrue(current["itens"][1]["ja_tenho"])
        self.assertEqual(self.request("/api/state")["resumo"]["lidos"], 2)
        self.assertEqual(plan_path.read_bytes(), signed_plan)
        self.assertTrue(app.download_cache.is_file())
        self.server.app = App(self.lib, app.storage)
        self.assertEqual(self.request("/api/library")["total"], 2)
        self.assertEqual({t["titulo"][0] for t in self.server.app.tracks}, {"Existente", "Nova"})
        self.request("/api/analyze", {}, status=202)
        self.wait_job()
        self.assertFalse(app.download_cache.exists())
        self.assertEqual(sha256(self.music), self.before)

    def test_fake_ids_and_browser_supplied_commands_or_metadata_are_refused(self):
        catalog = self.search()
        valid = {"catalogo": catalog["id"], "ids": [catalog["itens"][1]["id"]], "pasta": str(self.lib)}
        for change in ({"ids": []}, {"ids": ["../fora"]}, {"ids": [valid["ids"][0], valid["ids"][0]]}, {"catalogo": "../fora"}, {"catalogo": {}}, {"url": "https://evil.invalid"}, {"faixas": self.songs}, {"comando": "echo bad"}, {"bitrate": "invalid"}):
            with self.subTest(change=change):
                self.request("/api/baixar/baixar", {**valid, **change}, status=400)
        self.request("/api/baixar/abrir", {"catalogo": catalog["id"], "item": catalog["itens"][0]["id"]}, status=400)
        self.mock_download.assert_not_called()
        self.assertEqual(sha256(self.music), self.before)

    def test_search_inputs_destination_and_authentication_are_validated(self):
        valid = {"tipo": "musica", "consulta": "Banda Teste", "pasta": str(self.lib)}
        self.request("/api/baixar/buscar", valid, status=403, **{"X-Library-Token": "bad"})
        self.request("/api/baixar/buscar", valid, status=403, Origin="http://evil.invalid")
        self.request("/api/baixar/buscar", valid, status=403, Host="evil.invalid")
        for change in ({"tipo": "comando"}, {"consulta": ""}, {"consulta": []}, {"pasta": "relativa"}, {"pasta": str(self.music)}, {"pasta": str(self.base / "inexistente")}, {"pasta": str(self.lib) + "\n"}):
            with self.subTest(change=change):
                self.request("/api/baixar/buscar", {**valid, **change}, status=400)
        self.mock_songs.assert_not_called()
        self.assertIsNone(self.server.app.job)

    def test_link_search_and_provider_error_leave_existing_collection_untouched(self):
        self.search("link", "https://open.spotify.com/album/AlbumTeste")
        self.mock_tracks.assert_called_once_with("https://open.spotify.com/album/AlbumTeste")
        self.mock_songs.side_effect = RuntimeError("spotDL indisponível")
        self.request("/api/baixar/buscar", {"tipo": "musica", "consulta": "Nova", "pasta": str(self.lib)}, status=202)
        self.wait_job("erro")
        self.assertIn("spotDL indisponível", self.request("/api/state")["erro"])
        self.assertEqual(sha256(self.music), self.before)

    def test_query_is_a_job_and_blocks_concurrent_downloads(self):
        catalog = self.search()
        entered, release = threading.Event(), threading.Event()
        def slow_search(*args):
            entered.set()
            release.wait(5)
            return []
        self.mock_albums.side_effect = slow_search
        self.request("/api/baixar/buscar", {"tipo": "artista", "consulta": "Banda Teste", "pasta": str(self.lib)}, status=202)
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.request("/api/state")["job"]["estado"], "executando")
            self.request("/api/baixar")
            self.request("/api/baixar/baixar", {"catalogo": catalog["id"], "ids": [catalog["itens"][1]["id"]]}, status=400)
        finally:
            release.set()
            self.server.app.worker.join(timeout=5)
        self.mock_download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
