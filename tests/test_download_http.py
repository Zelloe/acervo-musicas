"""Browser selection uses server-owned IDs without passing arbitrary spotDL commands."""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1

from library_core import sha256
from library_plan import create_plan
from api_contract import to_api
from server import App, Handler


@unittest.skipUnless(shutil.which("ffmpeg"), "Tests generate temporary audio with ffmpeg")
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
        self.mock_songs = patch("download_picker.downloader.search_songs", return_value=self.songs).start()
        self.mock_tracks = patch("download_picker.downloader.tracks", return_value=self.songs).start()
        self.mock_albums = patch("download_picker.downloader.search_albums", return_value=[{"titulo": "Álbum", "artista": "Banda Teste", "ano": "2026"}]).start()
        self.mock_editions = patch("download_picker.downloader.editions", return_value=[{"titulo": "Álbum (Deluxe)", "url": "https://open.spotify.com/album/AlbumTeste"}, {"titulo": "Álbum", "url": "https://open.spotify.com/album/AlbumOriginal"}]).start()
        self.mock_download = patch("download_picker.downloader.download", return_value=[]).start()
        self.addCleanup(patch.stopall)

    def stop_server(self):
        self.server.shutdown()
        self.thread.join()

    def request(self, route, data=None, status=200, **headers):
        defaults = {"Content-Type": "application/json", "Origin": self.url, "X-Acervo-Language": "pt-BR", "X-Library-Token": self.server.app.token}
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

    def wait_job(self, state="completed"):
        self.server.app.worker.join(timeout=5)
        self.assertEqual(self.server.app.job['status'], state, self.server.app.job)
        return self.request("/api/download")['catalog']

    def search(self, kind="musica", text="Banda Teste"):
        self.request("/api/download/search", {"tipo": kind, "consulta": text, "pasta": str(self.lib)}, status=202)
        return self.wait_job()

    def test_artist_album_edition_tracks_and_cached_back_navigation(self):
        self.assertIsNone(self.request("/api/download")['catalog'])
        albums = self.search("artista")
        self.assertEqual(albums['type'], 'albums')
        self.request("/api/download/open", {"catalogo": albums["id"], "item": albums['items'][0]["id"]}, status=202)
        editions = self.wait_job()
        self.mock_editions.assert_called_once_with("Banda Teste", "Álbum")
        self.request("/api/download/open", {"catalogo": editions["id"], "item": editions['items'][0]["id"]}, status=202)
        tracks = self.wait_job()
        self.assertEqual(tracks['type'], 'tracks')
        self.assertEqual([row['already_owned'] for row in tracks['items']], [True, False])
        self.assertEqual(self.request("/api/download?catalogo=" + albums["id"])['catalog'], albums)
        self.assertNotIn("id", self.songs[0])
        self.assertEqual(sha256(self.music), self.before)

    def test_only_selected_server_rows_reach_downloader_and_chosen_destination(self):
        catalog = self.search()
        selected = catalog['items'][1]
        destination = self.base / "Outra pasta"
        destination.mkdir()
        self.mock_download.return_value = [{**selected, "estado": "baixado", "arquivo": str(destination / "nova.mp3")}]
        self.request("/api/download/download", {"catalogo": catalog["id"], "ids": [selected["id"]], "pasta": str(destination), "bitrate": "256k"}, status=202)
        self.wait_job()
        args, kwargs = self.mock_download.call_args
        self.assertEqual((to_api(args[0]), args[1]), ([selected], destination))
        self.assertEqual(kwargs["bitrate"], "256k")
        self.assertTrue(callable(kwargs['progress']))
        result = self.request("/api/download")['result']
        self.assertEqual((len(result), result[0]['title'], result[0]['status']), (1, "Nova", 'downloaded'))
        self.assertEqual(sha256(self.music), self.before)

    def test_single_edition_opens_tracks_without_an_extra_selection(self):
        self.mock_editions.return_value = [{"titulo": "Álbum", "url": "https://open.spotify.com/album/AlbumOriginal"}]
        albums = self.search("artista")
        self.request("/api/download/open", {"catalogo": albums["id"], "item": albums['items'][0]["id"]}, status=202)
        self.assertEqual(self.wait_job()['type'], 'tracks')
        self.mock_tracks.assert_called_once_with("https://open.spotify.com/album/AlbumOriginal")

    def test_new_downloads_update_only_their_inventory_and_survive_restart(self):
        from library_scan import read_track
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
        with patch("download_picker.downloader.local_index", side_effect=AssertionError("A coleção já está no inventário")), patch("server.create_plan", side_effect=AssertionError("Não reanalisar a coleção no download")), patch("server.read_track", wraps=read_track) as reader:
            catalog = self.search()
            self.request("/api/download/download", {"catalogo": catalog["id"], "ids": [catalog['items'][1]["id"]]}, status=202)
            current = self.wait_job()
            self.assertEqual(reader.call_args_list[0].args, (new_path,))
            self.assertEqual(reader.call_count, 1)
            self.assertTrue(current['items'][1]['already_owned'])
        self.assertEqual(self.request("/api/state")['summary']['read_count'], 2)
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
        valid = {"catalogo": catalog["id"], "ids": [catalog['items'][1]["id"]], "pasta": str(self.lib)}
        for change in ({"ids": []}, {"ids": ["../fora"]}, {"ids": [valid["ids"][0], valid["ids"][0]]}, {"catalogo": "../fora"}, {"catalogo": {}}, {"url": "https://evil.invalid"}, {"faixas": self.songs}, {"comando": "echo bad"}, {"bitrate": "invalid"}):
            with self.subTest(change=change):
                self.request("/api/download/download", {**valid, **change}, status=400)
        self.request("/api/download/open", {"catalogo": catalog["id"], "item": catalog['items'][0]["id"]}, status=400)
        self.mock_download.assert_not_called()
        self.assertEqual(sha256(self.music), self.before)

    def test_search_inputs_destination_and_authentication_are_validated(self):
        valid = {"tipo": "musica", "consulta": "Banda Teste", "pasta": str(self.lib)}
        self.request("/api/download/search", valid, status=403, **{"X-Library-Token": "bad"})
        self.request("/api/download/search", valid, status=403, Origin="http://evil.invalid")
        self.request("/api/download/search", valid, status=403, Host="evil.invalid")
        for change in ({"tipo": "comando"}, {"consulta": ""}, {"consulta": []}, {"pasta": "relativa"}, {"pasta": str(self.music)}, {"pasta": str(self.base / "inexistente")}, {"pasta": str(self.lib) + "\n"}):
            with self.subTest(change=change):
                self.request("/api/download/search", {**valid, **change}, status=400)
        self.mock_songs.assert_not_called()
        self.assertIsNone(self.server.app.job)

    def test_link_search_and_provider_error_leave_existing_collection_untouched(self):
        self.search("link", "https://open.spotify.com/album/AlbumTeste")
        self.mock_tracks.assert_called_once_with("https://open.spotify.com/album/AlbumTeste")
        self.mock_songs.side_effect = RuntimeError("spotDL indisponível")
        self.request("/api/download/search", {"tipo": "musica", "consulta": "Nova", "pasta": str(self.lib)}, status=202)
        self.wait_job("error")
        self.assertIn("spotDL indisponível", self.request("/api/state")['error'])
        self.assertEqual(sha256(self.music), self.before)

    def test_query_is_a_job_and_downloads_wait_for_it(self):
        catalog = self.search()
        entered, release = threading.Event(), threading.Event()
        def slow_search(*args):
            entered.set()
            release.wait(5)
            return []
        self.mock_albums.side_effect = slow_search
        self.request("/api/download/search", {"tipo": "artista", "consulta": "Banda Teste", "pasta": str(self.lib)}, status=202)
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.request("/api/state")["job"]['status'], 'running')
            self.request("/api/download")
            self.request("/api/download/download", {"catalogo": catalog["id"], "ids": [catalog['items'][1]["id"]]}, status=202)
            self.assertEqual(self.request("/api/state")["download_queue"][0]["status"], "waiting")
            self.mock_download.assert_not_called()
        finally:
            release.set()
        self.assertEqual(self.finished_queue()[0]["status"], "completed")
        self.mock_download.assert_called_once()

    def catalog(self, route="/api/catalog/search", **data):
        return self.request(route, {"pasta": str(self.lib), **data})["catalog"]

    def finished_queue(self):
        for _ in range(200):
            rows = self.request("/api/state")["download_queue"]
            if rows and all(row["status"] in ("completed", "error") for row in rows):
                return rows
            time.sleep(0.02)
        self.fail(rows)

    def test_browsing_does_not_need_or_disturb_the_running_job(self):
        app = self.server.app
        app.job = {"id": "x", "type": "direct_download", "status": "running", "message": "Baixando 1 de 9", "progress": None}
        try:
            albums = self.catalog(tipo="artista", consulta="Banda Teste")
            self.assertEqual(albums["type"], "albums")
            editions = self.catalog("/api/catalog/open", catalogo=albums["id"], item=albums["items"][0]["id"])
            self.assertEqual(editions["type"], "editions")
            tracks = self.catalog("/api/catalog/open", catalogo=editions["id"], item=editions["items"][0]["id"])
            self.assertEqual(tracks["type"], "tracks")
            self.assertEqual(self.catalog(tipo="musica", consulta="Banda Teste")["type"], "tracks")
            self.assertEqual(app.job["message"], "Baixando 1 de 9")
        finally:
            app.job = None
        self.request("/api/catalog/search", {"tipo": "outro", "consulta": "x", "pasta": str(self.lib)}, status=400)

    def test_artist_album_list_is_reused_for_a_while(self):
        self.catalog(tipo="artista", consulta="Banda Teste")
        self.catalog(tipo="artista", consulta="banda teste")
        self.mock_albums.assert_called_once()
        with patch("download_picker.LOOKUP_SECONDS", 0):
            self.catalog(tipo="artista", consulta="Banda Teste")
        self.assertEqual(self.mock_albums.call_count, 2)

    def test_queue_runs_batches_in_order_and_only_waiting_ones_can_be_removed(self):
        release = threading.Event()
        def slow(rows, *args, **kwargs):
            release.wait(5)
            return [{**row, "estado": "baixado" if index == 0 else "erro"} for index, row in enumerate(rows)]
        self.mock_download.side_effect = slow
        catalog = self.catalog(tipo="musica", consulta="Banda Teste")
        ids = [row["id"] for row in catalog["items"]]
        def add(chosen):
            self.request("/api/download/download", {"catalogo": catalog["id"], "ids": chosen, "pasta": str(self.lib)}, status=202)
        add(ids)
        add(ids[:1])
        add(ids[:1])
        rows = self.request("/api/state")["download_queue"]
        self.assertEqual([row["status"] for row in rows], ["downloading", "waiting", "waiting"])
        self.assertNotIn("prepared", rows[0])
        self.request("/api/download/queue/remove", {"id": rows[0]["id"]}, status=400)
        self.request("/api/download/queue/remove", {"id": rows[2]["id"]}, status=202)
        self.assertEqual(len(self.catalog(tipo="artista", consulta="Banda Teste")["items"]), 1)
        release.set()
        rows = self.finished_queue()
        self.assertEqual([(row["status"], row["total"], row["downloaded"], row["errors"]) for row in rows], [("completed", 2, 1, 1), ("completed", 1, 1, 0)])
        self.assertEqual(self.mock_download.call_count, 2)

    def test_failed_batch_does_not_stop_the_next_one(self):
        self.mock_download.side_effect = [ValueError("Sem rede"), []]
        catalog = self.catalog(tipo="musica", consulta="Banda Teste")
        chosen = {"catalogo": catalog["id"], "ids": [catalog["items"][0]["id"]], "pasta": str(self.lib)}
        app = self.server.app
        app.job = {"id": "x", "type": "analysis", "status": "running", "message": "", "progress": None}
        self.request("/api/download/download", chosen, status=202)
        self.request("/api/download/download", chosen, status=202)
        self.assertEqual({row["status"] for row in self.request("/api/state")["download_queue"]}, {"waiting"})
        app.job["status"] = "completed"
        rows = self.finished_queue()
        self.assertEqual([(row["status"], row["message"]) for row in rows[:1]], [("error", "Sem rede")])
        self.assertEqual(rows[1]["status"], "completed")

    def test_going_back_to_an_album_does_not_repeat_remote_lookups(self):
        for _ in range(2):
            albums = self.catalog(tipo="artista", consulta="Banda Teste")
            editions = self.catalog("/api/catalog/open", catalogo=albums["id"], item=albums["items"][0]["id"])
            tracks = self.catalog("/api/catalog/open", catalogo=editions["id"], item=editions["items"][0]["id"])
            self.assertEqual([row["already_owned"] for row in tracks["items"]], [True, False])
        self.mock_albums.assert_called_once()
        self.mock_editions.assert_called_once()
        self.mock_tracks.assert_called_once()

    def test_queue_starts_when_a_blocking_operation_ends(self):
        app = self.server.app
        catalog = self.catalog(tipo="musica", consulta="Banda Teste")
        chosen = {"catalogo": catalog["id"], "ids": [catalog["items"][0]["id"]], "pasta": str(self.lib)}
        ident = self.request("/api/search")["tracks"][0]["id"]
        started = threading.Event()
        release = threading.Event()
        def blocking_write(*args, **kwargs):
            started.set()
            release.wait(5)
            raise ValueError("Falha simulada")
        with patch("server.protections.protect", side_effect=blocking_write):
            worker = threading.Thread(target=lambda: self.request("/api/protections", {"ids": [ident], "campos": ["genero"], "proteger": True}, status=400))
            worker.start()
            self.assertTrue(started.wait(2))
            self.request("/api/download/download", chosen, status=202)
            self.assertEqual(app.download_queue[0]["estado"], "aguardando")
            self.mock_download.assert_not_called()
            release.set()
            worker.join(5)
        app.worker.join(timeout=5)
        self.mock_download.assert_called_once()

    def queue_behind_a_job(self, error=None):
        app = self.server.app
        catalog = self.catalog(tipo="musica", consulta="Banda Teste")
        release = threading.Event()
        def operation():
            release.wait(5)
            if error:
                raise ValueError(error)
            return {"pronto": True}
        app.start("lyrics_lookup", operation)
        worker = app.worker
        self.request("/api/download/download", {"catalogo": catalog["id"], "ids": [catalog["items"][0]["id"]], "pasta": str(self.lib)}, status=202)
        release.set()
        worker.join(5)
        return app

    def test_finished_job_is_seen_before_the_next_batch_replaces_it(self):
        app = self.queue_behind_a_job()
        first = self.request("/api/state")
        self.assertEqual((first["job"]["type"], first["job"]["status"]), ("lyrics_lookup", "completed"))
        self.assertEqual(first["download_queue"][0]["status"], "waiting")
        self.mock_download.assert_not_called()
        self.assertEqual(self.finished_queue()[0]["status"], "completed")
        self.mock_download.assert_called_once()

    def test_error_of_a_finished_job_is_seen_before_the_next_batch(self):
        self.queue_behind_a_job("Falha na busca")
        first = self.request("/api/state")
        self.assertEqual((first["job"]["status"], first["error"]), ("error", "Falha na busca"))
        self.finished_queue()

    def test_queue_continues_without_any_open_page(self):
        with patch("server.FINISHED_JOB_SECONDS", 0.05):
            app = self.queue_behind_a_job()
            for _ in range(200):
                if self.mock_download.called:
                    break
                time.sleep(0.02)
        self.mock_download.assert_called_once()

    def test_old_timer_does_not_release_a_newer_finished_job(self):
        with patch("server.FINISHED_JOB_SECONDS", 0.05):
            app = self.queue_behind_a_job()
            with app.lock:
                app.job = {**app.job, "id": "newer"}
            time.sleep(0.2)
        self.assertTrue(app.job_unseen)
        self.mock_download.assert_not_called()
        self.assertEqual(self.finished_queue()[0]["status"], "completed")

    def test_album_list_says_whether_a_cover_exists(self):
        app = self.server.app
        self.assertIs(self.request("/api/albums")["albums"][0]["has_cover"], False)
        app.tracks[0]["capas"] = [{"tipo": 3}]
        self.assertIs(self.request("/api/albums")["albums"][0]["has_cover"], True)

    def test_cover_can_be_revalidated_without_reading_tags_again(self):
        tags = ID3(self.music)
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=b"cover bytes"))
        tags.save(self.music, v2_version=3)
        ident = self.request("/api/search")["tracks"][0]["id"]
        url = f"{self.url}/api/cover?id={ident}"
        with urlopen(url, timeout=5) as response:
            self.assertEqual(response.read(), b"cover bytes")
            tag = response.headers["ETag"]
            self.assertIn("max-age", response.headers["Cache-Control"])
        with patch("server.ID3", side_effect=AssertionError("tags were read")):
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(url, headers={"If-None-Match": tag}), timeout=5)
            self.assertEqual(caught.exception.code, 304)
            self.assertEqual(caught.exception.headers["ETag"], tag)
            self.assertIsNone(caught.exception.headers["Content-Type"])
            self.assertIsNone(caught.exception.headers["Content-Length"])
            caught.exception.close()


if __name__ == "__main__":
    unittest.main()
