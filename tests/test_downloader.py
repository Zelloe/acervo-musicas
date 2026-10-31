from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mutagen.id3 import ID3, TIT2, TPE1

import downloader
from download_menu import parse_selection

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
        self.assertEqual(downloader.genero_por_regra("ac/dc", "Back In Black", RULES), "Hard Rock")
        self.assertEqual(downloader.genero_por_regra("Metallica", "Load (Remastered)", RULES), "Hard Rock")
        self.assertEqual(downloader.genero_por_regra("Metallica", "Master of Puppets", RULES), "Thrash Metal")
        self.assertEqual(downloader.genero_por_regra("Duplo", "X", RULES), "")
        self.assertEqual(downloader.genero_por_regra("Desconhecido", "X", RULES), "")

    def test_inputs_are_validated_before_running_spotdl(self):
        with self.assertRaises(ValueError):
            downloader.faixas("https://example.com/album/1")
        with self.assertRaises(ValueError):
            downloader.buscar_musicas("--output /etc")


@unittest.skipUnless(shutil.which("ffmpeg"), "Os testes de integração usam ffmpeg para gerar áudio temporário")
class DownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.TemporaryDirectory()
        cls.sample = Path(cls.samples.name) / "sample.mp3"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=0.1", "-c:a", "libmp3lame", str(cls.sample)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.samples.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.item = {"titulo": "Back In Black", "artista": "AC/DC", "album": "Back In Black", "url": "https://open.spotify.com/track/abc123"}

    def tearDown(self):
        self.temp.cleanup()

    def fake_spotdl(self, name="ACDC - Back In Black.mp3", returncode=0, corrupt=False, foreign=False):
        def run(command, cwd, **kwargs):
            self.assertEqual(command[1:3], ["download", self.item["url"]])
            self.assertNotEqual(Path(cwd), self.folder)  # o spotDL nunca escreve direto na coleção
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
        with patch.object(downloader, "spotdl_executable", return_value="spotdl"), patch.object(downloader.subprocess, "run", side_effect=self.wrap(self.fake_spotdl(**fake))):
            return downloader.baixar(items, self.folder, letras=False)

    def wrap(self, fake):
        real = subprocess.run
        def dispatch(command, *args, **kwargs):
            if command[0] == "spotdl":
                return fake(command, **kwargs)
            return real(command, *args, **kwargs)  # ffmpeg real valida o áudio
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
        self.assertIn("corrompido", result["mensagem"])
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
        with patch.object(downloader, "spotdl_executable", return_value="spotdl"), patch.object(downloader, "_publish", side_effect=publish), patch.object(downloader.subprocess, "run", side_effect=self.wrap(run)):
            results = downloader.baixar([self.item, other, third, fourth], self.folder, letras=False)
        self.assertEqual([r["estado"] for r in results], ["baixado", "erro", "erro", "nao_baixado"])
        self.assertEqual(self.listing(), ["ACDC - Back In Black.mp3"])

    def test_index_marks_existing_tracks(self):
        self.download([self.item])
        index = downloader.indice_local(self.folder)
        marked = downloader.ja_tenho([dict(self.item), {"titulo": "Back In Black - Remastered 2003", "artista": "AC/DC"}, {"titulo": "Hells Bells", "artista": "AC/DC"}], index)
        self.assertEqual([m["ja_tenho"] for m in marked], [True, True, False])

    def test_bad_link_is_reported_without_running(self):
        with patch.object(downloader, "spotdl_executable", return_value="spotdl"), patch.object(downloader.subprocess, "run") as run:
            result = downloader.baixar([{**self.item, "url": "https://open.spotify.com/album/x"}], self.folder, letras=False)
        run.assert_not_called()
        self.assertEqual(result[0]["estado"], "erro")

if __name__ == "__main__":
    unittest.main()
