from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1, TRCK

from playlists import album_order, collect, matches, validate_m3u8, write_m3u8


@unittest.skipUnless(shutil.which("ffmpeg"), "Os testes de integração usam ffmpeg para gerar áudio temporário")
class PlaylistTest(unittest.TestCase):
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
        self.base = Path(self.temp.name)
        self.lib = self.base / "Coleção"
        self.lib.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def mp3(self, relative: str, title: str, artist: str, album: str, track: str, genre: str) -> Path:
        path = self.lib / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.sample, path)
        tags = ID3()
        for frame in (TIT2(encoding=3, text=[title]), TPE1(encoding=3, text=[artist]), TALB(encoding=3, text=[album]), TRCK(encoding=3, text=[track]), TCON(encoding=3, text=[genre])):
            tags.add(frame)
        tags.save(path, v2_version=3)
        return path

    def library(self):
        self.mp3("Engenheiros do Hawaii/02 - Até o Fim.mp3", "Até o Fim", "Engenheiros do Hawaii", "Várias Variáveis", "2", "Rock Brasileiro")
        self.mp3("Engenheiros do Hawaii/01 - Herdeiro da Pampa Pobre.mp3", "Herdeiro da Pampa Pobre", "Engenheiros do Hawaii", "Várias Variáveis", "1/12", "Rock Brasileiro")
        self.mp3("#Hashtag/Ação.mp3", "Ação", "Banda #1", "Começo", "1", "Rock Brasileiro")
        self.mp3("Outros/Faixa.mp3", "Faixa", "Outra", "Outro", "1", "Jazz")
        tracks, errors = collect(self.lib)
        self.assertEqual(errors, [])
        return sorted((t for t in tracks if matches(t, "genero", "rock brasileiro")), key=album_order)

    def test_relative_utf8_paths_survive_moving_the_collection(self):
        tracks = self.library()
        self.assertEqual([t["titulo"][0] for t in tracks], ["Ação", "Herdeiro da Pampa Pobre", "Até o Fim"])
        playlist = self.lib / "Playlists" / "Rock Brasileiro.m3u8"
        result = write_m3u8(playlist, tracks, "Rock Brasileiro")
        self.assertEqual((result["entradas"], result["ausentes"]), (3, []))
        content = playlist.read_bytes().decode("utf-8")
        self.assertFalse(content.startswith("﻿"))
        self.assertNotIn("\r", content)
        self.assertNotIn(str(self.base), content)
        self.assertIn("../Engenheiros do Hawaii/02 - Até o Fim.mp3\n", content)
        self.assertIn("../#Hashtag/Ação.mp3\n", content)
        self.assertIn("#EXTINF:0,Engenheiros do Hawaii - Até o Fim\n", content)
        moved = self.base / "outro disco" / "Coleção"
        shutil.copytree(self.lib, moved)
        shutil.rmtree(self.lib)
        self.assertEqual(validate_m3u8(moved / "Playlists" / "Rock Brasileiro.m3u8")["ausentes"], [])

    def test_comment_like_path_is_prefixed(self):
        tracks = self.library()
        playlist = self.lib / "lista.m3u8"
        write_m3u8(playlist, tracks)
        self.assertIn("./#Hashtag/Ação.mp3", playlist.read_text(encoding="utf-8").splitlines())
        self.assertEqual(validate_m3u8(playlist)["encontradas"], 3)

    def test_existing_playlist_is_not_overwritten_and_missing_files_are_reported(self):
        tracks = self.library()
        playlist = self.base / "lista.m3u8"
        write_m3u8(playlist, tracks)
        with self.assertRaises(FileExistsError):
            write_m3u8(playlist, tracks[:1])
        Path(tracks[0]["arquivo"]).unlink()
        self.assertEqual(validate_m3u8(playlist)["ausentes"], ["Coleção/#Hashtag/Ação.mp3"])

    def test_temporary_copies_are_not_listed(self):
        self.library()
        shutil.copy(self.sample, self.lib / ".acervo-import-abc.mp3")
        tracks, _ = collect(self.lib)
        self.assertFalse(any(".acervo-import-" in t["arquivo"] for t in tracks))


if __name__ == "__main__":
    unittest.main()
