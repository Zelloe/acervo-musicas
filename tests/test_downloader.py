import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mutagen.id3 import ID3, TIT2, TPE1

from acervo.downloads import downloader, spotdl_search
from acervo.downloads.menu import parse_selection
from acervo.messages import language

ITEMS = [{"titulo": "A", "ja_tenho": True}, {"titulo": "B", "ja_tenho": False}, {"titulo": "C", "ja_tenho": False}]
RULES = [
    {"artista": "AC/DC", "album": "", "genero": "Hard Rock"},
    {"artista": "Metallica", "album": "", "genero": "Thrash Metal"},
    {"artista": "Metallica", "album": "Load", "genero": "Hard Rock"},
    {"artista": "Duplo", "album": "", "genero": "Rock"},
    {"artista": "Duplo", "album": "", "genero": "Pop"},
]


class SelectionTest(unittest.TestCase):
    def test_selection_forms(self):
        self.assertEqual(parse_selection("", ITEMS), [1, 2])
        self.assertEqual(parse_selection("faltantes", ITEMS), [1, 2])
        self.assertEqual(parse_selection("todas", ITEMS), [0, 1, 2])
        self.assertEqual(parse_selection("1-2, 3", ITEMS), [0, 1, 2])
        self.assertEqual(parse_selection("3,3,1", ITEMS), [2, 0])
        for bad in ("0", "4", "2-1", "a"):
            with self.assertRaises(ValueError):
                parse_selection(bad, ITEMS)

    def test_genre_rules_follow_analysis_priority(self):
        self.assertEqual(downloader.genre_from_rules("ac/dc", "Back In Black", RULES), "Hard Rock")
        self.assertEqual(downloader.genre_from_rules("Metallica", "Load (Remastered)", RULES), "Hard Rock")
        self.assertEqual(downloader.genre_from_rules("Metallica", "Master of Puppets", RULES), "Thrash Metal")
        self.assertEqual(downloader.genre_from_rules("Duplo", "X", RULES), "")
        self.assertEqual(downloader.genre_from_rules("Desconhecido", "X", RULES), "")

    def test_inputs_are_validated_before_running_spotdl(self):
        with self.assertRaises(ValueError):
            downloader.tracks("https://example.com/album/1")
        with self.assertRaises(ValueError):
            downloader.search_songs("--output /etc")


@unittest.skipUnless(shutil.which("ffmpeg"), "Os testes de integração usam ffmpeg para gerar áudio temporário")
class DownloadTest(unittest.TestCase):
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
                "sine=duration=0.1",
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
        self.folder = Path(self.temp.name)
        self.item = {
            "titulo": "Back In Black",
            "artista": "AC/DC",
            "album": "Back In Black",
            "url": "https://open.spotify.com/track/abc123",
        }

    def tearDown(self):
        self.temp.cleanup()

    def fake_spotdl(self, name="ACDC - Back In Black.mp3", returncode=0, corrupt=False, foreign=False):
        def run(command, cwd, **kwargs):
            self.assertEqual(command[1:3], ["download", self.item["url"]])
            self.assertNotEqual(Path(cwd), self.folder)  # spotDL never writes straight into the collection
            target = Path(cwd) / name
            if corrupt:
                data = self.sample.read_bytes()
                target.write_bytes(data[:200] + bytes(b ^ 0xFF for b in data[200:2000]))
            else:
                shutil.copy(self.sample, target)
                tags = ID3()
                tags.add(TIT2(encoding=3, text=[self.item["titulo"]]))
                tags.add(TPE1(encoding=3, text=[self.item["artista"]]))
                tags.save(target, v2_version=4)
            if foreign:
                (self.folder / name).write_bytes(b"outro programa")
            return subprocess.CompletedProcess(command, returncode, "", "erro simulado" if returncode else "")

        return run

    def download(self, items, **fake):
        with (
            patch.object(downloader, "spotdl_executable", return_value="spotdl"),
            patch.object(downloader.subprocess, "run", side_effect=self.wrap(self.fake_spotdl(**fake))),
        ):
            return downloader.download(items, self.folder, lyrics=False)

    def wrap(self, fake):
        real = subprocess.run

        def dispatch(command, *args, **kwargs):
            if command[0] == "spotdl":
                return fake(command, **kwargs)
            return real(command, *args, **kwargs)  # the real ffmpeg validates the audio

        return dispatch

    def listing(self):
        return sorted(p.name for p in self.folder.iterdir())

    def test_downloads_fill_genre_and_never_overwrite(self):
        first = self.download([self.item])[0]
        self.assertEqual(first["estado"], "baixado")
        self.assertEqual(first["genero_preenchido"], "Hard Rock")
        path = Path(first["arquivo"])
        self.assertEqual([str(v) for v in ID3(path)["TCON"].text], ["Hard Rock"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        content = path.read_bytes()
        second = self.download([self.item])[0]
        self.assertEqual(second["estado"], "ja_existia")
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(self.listing(), ["ACDC - Back In Black.mp3"])

    def test_failed_spotdl_with_partial_file_publishes_nothing(self):
        result = self.download([self.item], returncode=1)[0]
        self.assertEqual(result["estado"], "erro")
        self.assertEqual(self.listing(), [])

    def test_corrupted_audio_is_rejected(self):
        result = self.download([self.item], corrupt=True)[0]
        self.assertEqual(result["estado"], "erro")
        self.assertIn("corrupted", result["mensagem"])
        self.assertEqual(self.listing(), [])

    def test_name_that_appears_during_download_is_not_touched(self):
        result = self.download([self.item], foreign=True)[0]
        self.assertEqual(result["estado"], "ja_existia")
        self.assertEqual((self.folder / "ACDC - Back In Black.mp3").read_bytes(), b"outro programa")
        self.assertEqual(self.listing(), ["ACDC - Back In Black.mp3"])

    def test_publish_without_hard_links(self):
        with patch.object(downloader.os, "link", side_effect=OSError("exFAT")):
            result = self.download([self.item])[0]
        self.assertEqual(result["estado"], "baixado")
        self.assertEqual(Path(result["arquivo"]).stat().st_nlink, 1)
        self.assertEqual(self.listing(), ["ACDC - Back In Black.mp3"])

    def test_errors_keep_partial_results(self):
        other = {**self.item, "titulo": "Hells Bells", "url": "https://open.spotify.com/track/def456"}
        third = {**self.item, "titulo": "Shoot to Thrill", "url": "https://open.spotify.com/track/ghi789"}
        names = iter(["ACDC - Back In Black.mp3", "ACDC - Hells Bells.mp3", "ACDC - Shoot to Thrill.mp3"])
        real_publish = downloader._publish
        calls = []

        def publish(source, final):
            calls.append(final.name)
            if len(calls) == 2:
                raise ValueError("falha de validação simulada")
            if len(calls) == 3:
                raise OSError(28, "No space left on device")
            return real_publish(source, final)

        fourth = {**self.item, "titulo": "Hells Bells (Live)", "url": "https://open.spotify.com/track/jkl012"}

        def run(command, cwd, **kwargs):
            name = next(names)
            self.item = next(i for i in (self.item, other, third) if command[2] == i["url"])
            return self.fake_spotdl(name)(command, cwd)

        with (
            patch.object(downloader, "spotdl_executable", return_value="spotdl"),
            patch.object(downloader, "_publish", side_effect=publish),
            patch.object(downloader.subprocess, "run", side_effect=self.wrap(run)),
        ):
            results = downloader.download([self.item, other, third, fourth], self.folder, lyrics=False, workers=1)
        self.assertEqual([r["estado"] for r in results], ["baixado", "erro", "erro", "nao_baixado"])
        self.assertEqual(self.listing(), ["ACDC - Back In Black.mp3"])

    def test_index_marks_existing_tracks(self):
        self.download([self.item])
        index = downloader.local_index(self.folder)
        marked = downloader.mark_owned(
            [
                dict(self.item),
                {"titulo": "Back In Black - Remastered 2003", "artista": "AC/DC"},
                {"titulo": "Hells Bells", "artista": "AC/DC"},
            ],
            index,
        )
        self.assertEqual([m["ja_tenho"] for m in marked], [True, True, False])

    def test_bad_link_is_reported_without_running(self):
        with (
            patch.object(downloader, "spotdl_executable", return_value="spotdl"),
            patch.object(downloader.subprocess, "run") as run,
        ):
            result = downloader.download(
                [{**self.item, "url": "https://open.spotify.com/album/x"}], self.folder, lyrics=False
            )
        run.assert_not_called()
        self.assertEqual(result[0]["estado"], "erro")
        self.assertEqual(result[0]["mensagem"], "Invalid track link")

    def test_messages_follow_the_request_language(self):
        progress = []
        with (
            language("pt-BR"),
            patch.object(downloader, "spotdl_executable", return_value="spotdl"),
            patch.object(downloader.subprocess, "run") as run,
        ):
            result = downloader.download(
                [{**self.item, "url": "https://open.spotify.com/album/x"}],
                self.folder,
                progress=lambda text, _: progress.append(text),
                lyrics=False,
            )
        run.assert_not_called()
        self.assertEqual(result[0]["mensagem"], "Link de faixa inválido")
        self.assertEqual(progress, ["Baixando 1 de 1 · AC/DC - Back In Black", "Downloads concluídos"])


class ParallelDownloadTest(unittest.TestCase):
    ITEMS = [
        {"titulo": f"Track {n}", "artista": "Band", "url": f"https://open.spotify.com/track/t{n}"} for n in range(6)
    ]

    def test_parallel_messages_keep_the_request_language(self):
        def fetch(item):
            return {**item, "estado": "erro", "mensagem": downloader._("Invalid track link")}

        with language("pt-BR"):
            results = self.run_download(fetch)
        self.assertEqual({r["mensagem"] for r in results}, {"Link de faixa inválido"})

    def run_download(self, fetch, workers=3):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with (
            patch.object(downloader, "spotdl_executable", return_value="spotdl"),
            patch.object(downloader, "rules", return_value=[]),
            patch.object(downloader, "_download_one", side_effect=lambda item, *args: fetch(item)),
        ):
            return downloader.download(list(self.ITEMS), Path(temp.name), lyrics=False, workers=workers)

    def test_tracks_download_at_the_same_time_and_results_keep_their_order(self):
        running, peak, lock = [0], [0], threading.Lock()
        together = threading.Barrier(3, timeout=5)

        def fetch(item):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            if item["titulo"] in ("Track 0", "Track 1", "Track 2"):
                together.wait()
            time.sleep(0.01 * (6 - int(item["titulo"][-1])))
            with lock:
                running[0] -= 1
            return {**item, "estado": "baixado"}

        results = self.run_download(fetch)
        self.assertEqual(peak[0], 3)
        self.assertEqual([r["titulo"] for r in results], [i["titulo"] for i in self.ITEMS])

    def test_a_fatal_error_stops_the_tracks_that_have_not_started(self):
        second_started = threading.Event()

        def fetch(item):
            if item["titulo"] == "Track 0":
                self.assertTrue(second_started.wait(5))
                raise OSError(28, "No space left on device")
            second_started.set()
            time.sleep(0.05)
            return {**item, "estado": "baixado"}

        results = self.run_download(fetch, workers=2)
        states = [r["estado"] for r in results]
        self.assertEqual(states[0], "erro")
        self.assertEqual(states[1], "baixado")
        self.assertEqual(states[2:], ["nao_baixado"] * 4)


class AlbumListTest(unittest.TestCase):
    ROWS = [
        {"titulo": "Later Live (Live)", "ano": "2012", "tipo": "Album"},
        {"titulo": "Second Album", "ano": "1990", "tipo": "Album"},
        {"titulo": "Radio Bootleg", "ano": "2018", "tipo": "Album"},
        {"titulo": "First Album - Deluxe Edition", "ano": "1980", "tipo": "Album"},
        {"titulo": "Greatest Songs", "ano": "2001", "tipo": "Album"},
        {"titulo": "Concert", "ano": "1985", "tipo": "Album"},
    ]
    GROUPS = [
        {"title": "First Album", "secondary-types": []},
        {"title": "Second Album", "secondary-types": ["Live"]},
        {"title": "Second Album", "secondary-types": []},
        {"title": "Later Live", "secondary-types": ["Live"]},
        {"title": "Greatest Songs", "secondary-types": ["Compilation"]},
        {"title": "Concert", "secondary-types": ["Live"]},
    ]

    def search(self, groups):
        rows = [dict(row) for row in self.ROWS]
        with (
            patch("acervo.downloads.downloader._adapter", return_value=rows),
            patch("acervo.downloads.downloader.MusicBrainz") as client,
        ):
            if isinstance(groups, Exception):
                client.return_value.release_groups.side_effect = groups
            else:
                client.return_value.release_groups.return_value = groups
            return downloader.search_albums("Band")

    def test_confirmed_studio_albums_come_first_in_release_order(self):
        result = self.search(self.GROUPS)
        self.assertEqual([r["titulo"] for r in result[:2]], ["First Album - Deluxe Edition", "Second Album"])
        by_title = {r["titulo"]: r["versao"] for r in result}
        self.assertEqual(by_title["Second Album"]["tipo"], "estudio")
        self.assertEqual(by_title["Concert"]["tipo"], "ao_vivo")
        self.assertIn("coletanea", by_title["Greatest Songs"]["marcas"])
        self.assertEqual(result[-1]["titulo"], "Radio Bootleg")
        self.assertEqual(result[-1]["versao"]["marcas"], ["nao_confirmado"])
        self.assertTrue(all("_sem_confirmacao" not in r for r in result))

    def test_without_an_answer_the_estimate_from_names_stays(self):
        for groups in (None, [], ValueError("offline"), OSError("cache")):
            with self.subTest(groups=groups):
                by_title = {r["titulo"]: r["versao"] for r in self.search(groups)}
                self.assertEqual(by_title["Radio Bootleg"]["tipo"], "estudio")
                self.assertEqual(by_title["Later Live (Live)"]["tipo"], "ao_vivo")

    def test_artist_page_lists_more_than_the_text_search(self):
        fake = MagicMock()
        fake.search.side_effect = lambda text, filter, limit: (
            [{"artist": "Other", "browseId": "x"}, {"artist": "band", "browseId": "b"}]
            if filter == "artists"
            else [{"title": "Tribute", "artists": [{"name": "Band"}]}]
        )
        fake.get_artist.return_value = {
            "albums": {"browseId": "all", "params": "p", "results": [{"title": "Only preview"}]},
            "singles": {"results": [{"title": "Small EP", "type": "EP"}, {"title": "One song", "type": "Single"}]},
        }
        fake.get_artist_albums.return_value = [
            {"title": "Full One", "year": "1990", "type": "Album"},
            {"title": "Band Karaoke", "type": "Album"},
            {"title": "full one", "type": "Album"},
        ]
        with patch("acervo.downloads.spotdl_search.ytmusic", return_value=fake):
            self.assertEqual([a["titulo"] for a in spotdl_search.albums("Band")], ["Full One", "Small EP"])
        fake.get_artist.assert_called_once_with("b")

    def test_bands_with_the_same_name_are_both_listed(self):
        fake = MagicMock()
        fake.search.return_value = [{"artist": "Band", "browseId": "a"}, {"artist": "Band", "browseId": "b"}]
        fake.get_artist.side_effect = lambda ident: {
            "singles": {"results": []},
            "albums": {"results": [{"title": "From " + ident}]},
        }
        with patch("acervo.downloads.spotdl_search.ytmusic", return_value=fake):
            self.assertEqual([a["titulo"] for a in spotdl_search.albums("Band")], ["From a", "From b"])

    def test_text_search_remains_when_the_artist_page_fails(self):
        fake = MagicMock()
        fake.search.side_effect = lambda text, filter, limit: (
            [{"title": "Found", "artists": [{"name": "Band"}]}, {"title": "Cover", "artists": [{"name": "Other"}]}]
            if filter == "albums"
            else [{"artist": "Band", "browseId": "b"}]
        )
        fake.get_artist.side_effect = KeyError("layout changed")
        with patch("acervo.downloads.spotdl_search.ytmusic", return_value=fake):
            self.assertEqual([a["titulo"] for a in spotdl_search.albums("Band")], ["Found"])

    def test_one_unavailable_namesake_keeps_the_other_artist_albums(self):
        fake = MagicMock()
        fake.search.return_value = [
            {"artist": "Band", "browseId": "unavailable"},
            {"artist": "Band", "browseId": "available"},
        ]

        def page(ident):
            if ident == "unavailable":
                raise KeyError("artist page unavailable")
            return {"albums": {"results": [{"title": "Available Album"}]}}

        fake.get_artist.side_effect = page
        with patch("acervo.downloads.spotdl_search.ytmusic", return_value=fake):
            self.assertEqual([a["titulo"] for a in spotdl_search.albums("Band")], ["Available Album"])
        self.assertEqual(fake.search.call_count, 1, "Do not discard the artist catalog for a shorter text search")

    def test_unavailable_pagination_keeps_preview_and_other_sections(self):
        fake = MagicMock()
        fake.search.return_value = [{"artist": "Band", "browseId": "artist"}]
        fake.get_artist.return_value = {
            "albums": {"browseId": "all", "params": "p", "results": [{"title": "Available Preview"}]},
            "singles": {"results": [{"title": "Available EP", "type": "EP"}]},
        }
        fake.get_artist_albums.side_effect = KeyError("pagination unavailable")
        with patch("acervo.downloads.spotdl_search.ytmusic", return_value=fake):
            self.assertEqual([a["titulo"] for a in spotdl_search.albums("Band")], ["Available Preview", "Available EP"])
        self.assertEqual(fake.search.call_count, 1)


class LookupProcessTest(unittest.TestCase):
    def process(self, **call):
        process = MagicMock()
        process.lock = threading.Lock()
        process._call.configure_mock(**call)
        patcher = patch("acervo.downloads.downloader._lookup_process", process)
        patcher.start()
        self.addCleanup(patcher.stop)
        return process

    def test_lookups_reuse_the_open_process(self):
        process = self.process(return_value=[{"titulo": "A"}])
        with patch("acervo.downloads.downloader._adapter_once") as once:
            self.assertEqual(downloader._adapter("faixas", "url"), [{"titulo": "A"}])
            self.assertEqual(downloader._adapter("faixas", "url"), [{"titulo": "A"}])
        once.assert_not_called()
        self.assertEqual(process._call.call_count, 2)
        downloader._adapter("faixas", "url", timeout=300)
        self.assertEqual(process.timeout, 300)

    def test_failed_process_is_closed_and_the_lookup_retries_from_a_clean_start(self):
        process = self.process(side_effect=RuntimeError("session expired"))
        with patch("acervo.downloads.downloader._adapter_once", return_value=[{"titulo": "B"}]) as once:
            self.assertEqual(downloader._adapter("faixas", "url", timeout=300), [{"titulo": "B"}])
        process.close.assert_called_once()
        once.assert_called_once_with("faixas", "url", timeout=300)

    def test_spotify_client_is_started_only_once_in_a_process(self):
        spotify = MagicMock()
        modules = {
            "spotdl": MagicMock(),
            "spotdl.utils": MagicMock(),
            "spotdl.utils.spotify": MagicMock(SpotifyClient=spotify),
        }
        with patch.dict(sys.modules, modules), patch("acervo.downloads.spotdl_search._spotify", None):
            self.assertIs(spotdl_search.client(), spotdl_search.client())
        spotify.init.assert_called_once()


if __name__ == "__main__":
    unittest.main()
