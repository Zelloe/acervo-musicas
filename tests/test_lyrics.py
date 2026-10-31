import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TIT2, TPE1, USLT

import downloader
from library_import import restore_import
from messages import language
from lyrics import bulk_lookup, prepare_text, save_lyrics_batch, LyricsClient, embed_plain, save_lyrics, current_lyrics, read_embedded, parse_lrc

LRC = "[00:00.00] 作词 : Johnson\n[00:27.24]Back in black\n[00:31.00][01:40.50]I hit the sack\n[ar:AC/DC]\nsem tempo\n"


class FakeLRCLIB:
    """Mimics LRCLIB: returns answers in order and records the requested URLs."""

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
        return LyricsClient(self.dir / "cache", opener=fake, min_interval=0, sleep=lambda s: None)

    def test_parse_lrc_drops_credits_and_orders_repeated_lines(self):
        self.assertEqual(parse_lrc(LRC), [{"t": 27.24, "linha": "Back in black"}, {"t": 31.0, "linha": "I hit the sack"}, {"t": 100.5, "linha": "I hit the sack"}])

    def test_exact_match_and_cache(self):
        fake = FakeLRCLIB(HIT)
        client = self.client(fake)
        first = client.lookup("AC/DC", "Back In Black", "Back In Black", 255.4)
        self.assertTrue(first["encontrada"])
        self.assertEqual(first["sincronizada"][0], {"t": 27.24, "linha": "Back in black"})
        self.assertEqual(first["texto"], "Back in black\nI hit the sack")
        self.assertIn("duration=255", fake.urls[0])
        self.assertEqual(client.lookup("ac/dc", "back in black", "Back In Black", 255.4), first)
        self.assertEqual(len(fake.urls), 1)  # the second one came from the cache

    def test_search_fallback_requires_same_song_and_close_duration(self):
        wrong = {**HIT, "id": 1, "duration": 300}
        other = {**HIT, "id": 2, "trackName": "Back In Black (Live)"}
        good = {**HIT, "id": 3, "duration": 257, "syncedLyrics": ""}
        fake = FakeLRCLIB(404, [wrong, other, good])
        result = self.client(fake).lookup("AC/DC", "Back In Black", "Outro Álbum", 255)
        self.assertEqual(result["url"], "https://lrclib.net/api/get/3")
        self.assertEqual(result["sincronizada"], [])
        self.assertIn("/api/search?", fake.urls[1])

    def test_retries_busy_service_and_does_not_cache_failures(self):
        fake = FakeLRCLIB(503, 503, HIT)
        self.assertTrue(self.client(fake).lookup("AC/DC", "Back In Black", "", 255)["encontrada"])
        cached = len(list((self.dir / "cache").glob("*.json")))
        fake = FakeLRCLIB(*[urllib.error.URLError("sem rede")] * 4)
        with self.assertRaisesRegex(RuntimeError, "No connection"):
            self.client(fake).lookup("Kyuss", "Gardenia", "", 413)
        fake = FakeLRCLIB(*[urllib.error.URLError("sem rede")] * 4)
        with language("pt-BR"), self.assertRaisesRegex(RuntimeError, "^Sem conexão com o serviço de letras$"):
            self.client(fake).lookup("Kyuss", "Gardenia", "", 413)
        self.assertEqual(len(list((self.dir / "cache").glob("*.json"))), cached)  # a failure is not cached
        fake = FakeLRCLIB(404, [])
        self.assertEqual(self.client(fake).lookup("Kyuss", "Gardenia", "", 413), {"encontrada": False})

    def test_instrumental(self):
        result = self.client(FakeLRCLIB({**HIT, "instrumental": True, "plainLyrics": None, "syncedLyrics": None})).lookup("Metallica", "Orion", "", 507)
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
        self.assertEqual(read_embedded(plain)["texto"], "Linha um\nLinha dois")
        synced = self.dir / "synced.mp3"
        shutil.copy(self.mp3, synced)
        tags = ID3()
        tags.add(USLT(encoding=3, lang="eng", desc="", text=LRC))
        tags.save(synced)
        self.assertEqual(len(read_embedded(synced)["sincronizada"]), 3)
        flac = self.dir / "f.flac"
        shutil.copy(self.flac, flac)
        audio = FLAC(flac)
        audio["LYRICS"] = ["Verso do FLAC"]
        audio.save()
        self.assertEqual(read_embedded(flac)["texto"], "Verso do FLAC")
        side = self.dir / "side.mp3"
        shutil.copy(self.mp3, side)
        self.assertIsNone(read_embedded(side))
        side.with_suffix(".lrc").write_text("[00:01.00]Do arquivo .lrc\n", encoding="utf-8")
        self.assertEqual(read_embedded(side)["fonte"], "arquivo")

    def test_embed_only_into_files_without_lyrics(self):
        path = self.dir / "novo.mp3"
        shutil.copy(self.mp3, path)
        found = {"encontrada": True, "instrumental": False, "texto": "Back in black", "sincronizada": []}
        self.assertTrue(embed_plain(path, found))
        self.assertFalse(embed_plain(path, {**found, "texto": "outra"}))
        self.assertEqual(ID3(path).getall("USLT")[0].text, "Back in black")
        self.assertFalse(embed_plain(path, {**found, "instrumental": True}))

    def test_write_lyrics_with_backup_and_exact_undo(self):
        from mutagen.id3 import APIC
        library, storage = self.dir / "musicas", self.dir / "dados"
        library.mkdir()
        mp3 = library / "faixa.mp3"
        shutil.copy(self.mp3, mp3)
        tags = ID3()
        tags.add(TIT2(encoding=3, text=["Back In Black"]))
        tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=b"capa"))
        tags.save(mp3, v2_version=3)
        flac = library / "faixa.flac"
        shutil.copy(self.flac, flac)
        originals = {p: p.read_bytes() for p in (mp3, flac)}
        manifests = []
        for path in (mp3, flac):
            manifests.append(save_lyrics(library, storage, str(path), "Back in black\r\nI hit the sack")["manifesto"])
            self.assertEqual(current_lyrics(path), ["Back in black\nI hit the sack"])
        self.assertEqual(ID3(mp3).getall("APIC")[0].data, b"capa")
        self.assertEqual(ID3(mp3).getall("TIT2")[0].text, ["Back In Black"])
        save_lyrics(library, storage, str(mp3), "Letra corrigida")  # replace existing lyrics
        self.assertEqual(current_lyrics(mp3), ["Letra corrigida"])
        with self.assertRaisesRegex(ValueError, "exactly these lyrics"):
            save_lyrics(library, storage, str(mp3), "Letra corrigida")
        restore_import(Path(manifests[1]))
        self.assertEqual(flac.read_bytes(), originals[flac])
        with self.assertRaisesRegex(ValueError, "later edits"):
            restore_import(Path(manifests[0]))  # the lyrics changed afterwards; does not overwrite
        self.assertEqual(current_lyrics(mp3), ["Letra corrigida"])
        with self.assertRaises(ValueError):
            save_lyrics(library, storage, str(self.dir / "fora.mp3"), "x")

    def test_synced_lyrics_are_written_as_tidy_lrc_and_read_back(self):
        library, storage = self.dir / "musicas", self.dir / "dados"
        library.mkdir()
        mp3, flac = library / "faixa.mp3", library / "faixa.flac"
        shutil.copy(self.mp3, mp3)
        shutil.copy(self.flac, flac)
        for path in (mp3, flac):
            save_lyrics(library, storage, str(path), "[00:00.00] 作词 : Johnson\r\n[ar:AC/DC]\n[00:27.24]Back in black\n[01:40.5]I hit the sack")
            self.assertEqual(current_lyrics(path), ["[00:27.24]Back in black\n[01:40.50]I hit the sack"])
            embedded = read_embedded(path)
            self.assertEqual(embedded["sincronizada"], [{"t": 27.24, "linha": "Back in black"}, {"t": 100.5, "linha": "I hit the sack"}])
            self.assertEqual(embedded["texto"], "Back in black\nI hit the sack")

    def test_lrc_formatting_is_idempotent_at_minute_boundaries(self):
        once = prepare_text("[00:59.999]Boundary verse\n[01:59.995]Next")
        self.assertEqual(once, "[01:00.00]Boundary verse\n[02:00.00]Next")
        self.assertEqual(prepare_text(once), once)

    def test_batch_save_is_one_transaction_with_one_undo(self):
        library, storage = self.dir / "musicas", self.dir / "dados"
        library.mkdir()
        tracks = []
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            shutil.copy(self.mp3, library / name)
            tracks.append(library / name)
        save_lyrics(library, storage, str(tracks[2]), "Igual")
        originals = {p: p.read_bytes() for p in tracks}
        result = save_lyrics_batch(library, storage, [
            {"file": str(tracks[0]), "text": "[00:01.00]Um"},
            {"file": str(tracks[1]), "text": "Dois"},
            {"file": str(tracks[2]), "text": "Igual"},
        ])
        self.assertEqual(result["arquivos_alterados"], 2)
        self.assertEqual(result["skipped"], [str(tracks[2])])
        self.assertEqual([current_lyrics(p) for p in tracks], [["[00:01.00]Um"], ["Dois"], ["Igual"]])
        restore_import(Path(result["manifesto"]))
        self.assertEqual({p: p.read_bytes() for p in tracks}, originals)
        with self.assertRaisesRegex(ValueError, "already have exactly"):
            save_lyrics_batch(library, storage, [{"file": str(tracks[2]), "text": "Igual"}])
        with self.assertRaises(ValueError):
            save_lyrics_batch(library, storage, [{"file": str(self.dir / "fora.mp3"), "text": "x"}])

    def test_bulk_lookup_classifies_and_stops_when_the_service_is_down(self):
        library = self.dir / "musicas"
        library.mkdir()
        synced = library / "synced.mp3"
        shutil.copy(self.mp3, synced)
        synced.with_suffix(".lrc").write_text("[00:01.00]Já sincronizada\n", encoding="utf-8")
        plain = library / "plain.mp3"
        shutil.copy(self.mp3, plain)
        tags = ID3()
        tags.add(USLT(encoding=3, lang="eng", desc="", text="Só texto"))
        tags.save(plain)

        def track(path, title):
            return {"id": title, "arquivo": str(path), "artista": ["Banda"], "titulo": [title], "album": ["Disco"], "duracao_segundos": 100}

        answers = {
            "Plain upgrade": {"encontrada": True, "instrumental": False, "sincronizada": [{"t": 1.0, "linha": "Verso"}], "texto": "Verso"},
            "Text only": {"encontrada": True, "instrumental": False, "sincronizada": [], "texto": "Linha"},
            "Orion": {"encontrada": True, "instrumental": True, "sincronizada": [], "texto": ""},
            "Missing": {"encontrada": False},
        }

        class Client:
            def __init__(self):
                self.calls = []

            def lookup(self, artist, title, album, duration):
                self.calls.append(title)
                if title.startswith("Down"):
                    raise RuntimeError("503")
                return answers[title]

        client = Client()
        missing = library / "missing.mp3"
        shutil.copy(self.mp3, missing)
        in_tag = library / "in_tag.mp3"
        shutil.copy(self.mp3, in_tag)
        tags = ID3()
        tags.add(USLT(encoding=3, lang="eng", desc="", text="[00:02.00]Dentro da tag"))
        tags.save(in_tag)
        rows = [track(in_tag, "Already"), track(synced, "Sidecar"), track(plain, "Plain upgrade"), track(missing, "Text only"), track(missing, "Orion"), track(missing, "Missing")]
        rows += [track(missing, f"Down {i}") for i in range(3)] + [track(missing, "Text only")]
        result = bulk_lookup(rows, client, max_failures=3)
        self.assertEqual([r["status"] for r in result], ["already_synced", "synced", "synced", "plain", "instrumental", "not_found", "error", "error", "error", "not_checked"])
        self.assertEqual((result[1]["text"], result[1]["source"], result[1]["current"]), ("[00:01.00]Já sincronizada", "lrc_file", "none"))
        self.assertEqual(result[2]["text"], "[00:01.00]Verso")
        self.assertEqual(result[2]["current"], "plain")
        self.assertNotIn("Already", client.calls)
        self.assertNotIn("Sidecar", client.calls)
        cancel = threading.Event()
        cancel.set()
        self.assertEqual({r["status"] for r in bulk_lookup(rows[:2], client, cancel=cancel)}, {"not_checked"})

    def test_write_refuses_track_changed_since_preview(self):
        from library_core import sha256
        library, storage = self.dir / "musicas", self.dir / "dados"
        library.mkdir()
        track = library / "faixa.mp3"
        shutil.copy(self.mp3, track)
        seen = sha256(track)
        tags = ID3()
        tags.add(TIT2(encoding=3, text=["Editada por outro programa"]))
        tags.save(track)
        edited = track.read_bytes()
        with self.assertRaisesRegex(ValueError, "changed since the preview"):
            save_lyrics(library, storage, str(track), "Letra", previous_sha256=seen)
        self.assertEqual(track.read_bytes(), edited)
        with self.assertRaisesRegex(ValueError, "preview hash"):
            save_lyrics(library, storage, str(track), "Letra", previous_sha256="../x")
        save_lyrics(library, storage, str(track), "Letra", previous_sha256=sha256(track))
        self.assertEqual(current_lyrics(track), ["Letra"])

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

            def lookup(self, *args):
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result

        with patch.object(downloader, "spotdl_executable", return_value="spotdl"), patch.object(downloader.subprocess, "run", side_effect=run):
            done = downloader.download([item], self.dir, lyrics=Client({"encontrada": True, "instrumental": False, "texto": "Back in black", "sincronizada": []}))[0]
            self.assertTrue(done["letra"])
            self.assertEqual(ID3(done["arquivo"]).getall("USLT")[0].text, "Back in black")
            other = self.dir / "outra"
            other.mkdir()
            failed = downloader.download([item], other, lyrics=Client(RuntimeError("Sem conexão")))[0]
        self.assertEqual((failed["estado"], failed["letra"]), ("baixado", False))


if __name__ == "__main__":
    unittest.main()
