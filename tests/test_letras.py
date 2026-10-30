import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TIT2, TPE1, USLT

import baixador
from letras import Letras, embutir_texto, ler_embutida, parse_lrc

LRC = "[00:00.00] 作词 : Johnson\n[00:27.24]Back in black\n[00:31.00][01:40.50]I hit the sack\n[ar:AC/DC]\nsem tempo\n"


class FakeLRCLIB:
    """Imita o LRCLIB: devolve respostas na ordem e registra as URLs pedidas."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls = []

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        answer = self.answers.pop(0)
        if isinstance(answer, int):
            raise urllib.error.HTTPError(request.full_url, answer, "erro", {}, None)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


HIT = {"id": 7, "trackName": "Back In Black", "artistName": "AC/DC", "duration": 255, "instrumental": False, "plainLyrics": "Back in black\nI hit the sack", "syncedLyrics": "[00:27.24]Back in black\n[00:31.00]I hit the sack"}


class LyricsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def client(self, fake):
        return Letras(self.dir / "cache", opener=fake, min_interval=0, sleep=lambda s: None)

    def test_parse_lrc_drops_credits_and_orders_repeated_lines(self):
        self.assertEqual(parse_lrc(LRC), [{"t": 27.24, "linha": "Back in black"}, {"t": 31.0, "linha": "I hit the sack"}, {"t": 100.5, "linha": "I hit the sack"}])

    def test_exact_match_and_cache(self):
        fake = FakeLRCLIB(HIT)
        client = self.client(fake)
        first = client.buscar("AC/DC", "Back In Black", "Back In Black", 255.4)
        self.assertTrue(first["encontrada"])
        self.assertEqual(first["sincronizada"][0], {"t": 27.24, "linha": "Back in black"})
        self.assertEqual(first["texto"], "Back in black\nI hit the sack")
        self.assertIn("duration=255", fake.urls[0])
        self.assertEqual(client.buscar("ac/dc", "back in black", "Back In Black", 255.4), first)
        self.assertEqual(len(fake.urls), 1)  # a segunda veio do cache

    def test_search_fallback_requires_same_song_and_close_duration(self):
        wrong = {**HIT, "id": 1, "duration": 300}
        other = {**HIT, "id": 2, "trackName": "Back In Black (Live)"}
        good = {**HIT, "id": 3, "duration": 257, "syncedLyrics": ""}
        fake = FakeLRCLIB(404, [wrong, other, good])
        result = self.client(fake).buscar("AC/DC", "Back In Black", "Outro Álbum", 255)
        self.assertEqual(result["url"], "https://lrclib.net/api/get/3")
        self.assertEqual(result["sincronizada"], [])
        self.assertIn("/api/search?", fake.urls[1])

    def test_retries_busy_service_and_does_not_cache_failures(self):
        fake = FakeLRCLIB(503, 503, HIT)
        self.assertTrue(self.client(fake).buscar("AC/DC", "Back In Black", "", 255)["encontrada"])
        cached = len(list((self.dir / "cache").glob("*.json")))
        fake = FakeLRCLIB(*[urllib.error.URLError("sem rede")] * 4)
        with self.assertRaisesRegex(RuntimeError, "Sem conexão"):
            self.client(fake).buscar("Kyuss", "Gardenia", "", 413)
        self.assertEqual(len(list((self.dir / "cache").glob("*.json"))), cached)  # falha não fica no cache
        fake = FakeLRCLIB(404, [])
        self.assertEqual(self.client(fake).buscar("Kyuss", "Gardenia", "", 413), {"encontrada": False})

    def test_instrumental(self):
        result = self.client(FakeLRCLIB({**HIT, "instrumental": True, "plainLyrics": None, "syncedLyrics": None})).buscar("Metallica", "Orion", "", 507)
        self.assertTrue(result["encontrada"] and result["instrumental"])


@unittest.skipUnless(shutil.which("ffmpeg"), "Os testes de integração usam ffmpeg para gerar áudio temporário")
class EmbeddedLyricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.TemporaryDirectory()
        root = Path(cls.samples.name)
        for ext, codec in (("mp3", "libmp3lame"), ("flac", "flac")):
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=0.1", "-c:a", codec, str(root / f"s.{ext}")], check=True)
        cls.mp3, cls.flac = root / "s.mp3", root / "s.flac"

    @classmethod
    def tearDownClass(cls):
        cls.samples.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_tags_and_sidecar(self):
        plain = self.dir / "plain.mp3"
        shutil.copy(self.mp3, plain)
        tags = ID3()
        tags.add(USLT(encoding=3, lang="eng", desc="", text="Linha um\nLinha dois"))
        tags.save(plain)
        self.assertEqual(ler_embutida(plain)["texto"], "Linha um\nLinha dois")
        synced = self.dir / "synced.mp3"
        shutil.copy(self.mp3, synced)
        tags = ID3()
        tags.add(USLT(encoding=3, lang="eng", desc="", text=LRC))
        tags.save(synced)
        self.assertEqual(len(ler_embutida(synced)["sincronizada"]), 3)
        flac = self.dir / "f.flac"
        shutil.copy(self.flac, flac)
        audio = FLAC(flac)
        audio["LYRICS"] = ["Verso do FLAC"]
        audio.save()
        self.assertEqual(ler_embutida(flac)["texto"], "Verso do FLAC")
        side = self.dir / "side.mp3"
        shutil.copy(self.mp3, side)
        self.assertIsNone(ler_embutida(side))
        side.with_suffix(".lrc").write_text("[00:01.00]Do arquivo .lrc\n", encoding="utf-8")
        self.assertEqual(ler_embutida(side)["fonte"], "arquivo")

    def test_embed_only_into_files_without_lyrics(self):
        path = self.dir / "novo.mp3"
        shutil.copy(self.mp3, path)
        found = {"encontrada": True, "instrumental": False, "texto": "Back in black", "sincronizada": []}
        self.assertTrue(embutir_texto(path, found))
        self.assertFalse(embutir_texto(path, {**found, "texto": "outra"}))
        self.assertEqual(ID3(path).getall("USLT")[0].text, "Back in black")
        self.assertFalse(embutir_texto(path, {**found, "instrumental": True}))

    def test_download_embeds_lyrics_and_survives_lyrics_failure(self):
        item = {"titulo": "Back In Black", "artista": "AC/DC", "album": "Back In Black", "duracao": 255, "url": "https://open.spotify.com/track/abc123"}
        real = subprocess.run

        def run(command, *args, **kwargs):
            if command[0] != "spotdl":
                return real(command, *args, **kwargs)
            target = Path(kwargs["cwd"]) / "ACDC - Back In Black.mp3"
            shutil.copy(self.mp3, target)
            tags = ID3()
            tags.add(TIT2(encoding=3, text=["Back In Black"]))
            tags.add(TPE1(encoding=3, text=["AC/DC"]))
            tags.save(target)
            return subprocess.CompletedProcess(command, 0, "", "")

        class Client:
            def __init__(self, result):
                self.result = result

            def buscar(self, *args):
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result

        with patch.object(baixador, "spotdl_executable", return_value="spotdl"), patch.object(baixador.subprocess, "run", side_effect=run):
            done = baixador.baixar([item], self.dir, letras=Client({"encontrada": True, "instrumental": False, "texto": "Back in black", "sincronizada": []}))[0]
            self.assertTrue(done["letra"])
            self.assertEqual(ID3(done["arquivo"]).getall("USLT")[0].text, "Back in black")
            other = self.dir / "outra"
            other.mkdir()
            failed = baixador.baixar([item], other, letras=Client(RuntimeError("Sem conexão")))[0]
        self.assertEqual((failed["estado"], failed["letra"]), ("baixado", False))


if __name__ == "__main__":
    unittest.main()
