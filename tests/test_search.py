from pathlib import Path
import sys
import tempfile
import textwrap
import time
import unittest

from search import GlobalSearch, LibraryIndex, normalize


def track(ident, title, artist, album, genre="", album_artist=""):
    return {"id": ident, "titulo": [title], "artista": [artist], "artista_album": [album_artist] if album_artist else [], "album": [album], "genero": [genre] if genre else []}


TRACKS = [
    track("1", "Back In Black", "AC/DC", "Back In Black", "Hard Rock"),
    track("2", "Hells Bells", "AC/DC", "Back In Black", "Hard Rock"),
    track("3", "Forty Six & 2", "TOOL", "Ænima", "Progressive Metal"),
    track("4", "Battery", "Metallica", "Master of Puppets", "Thrash Metal"),
    track("5", "Herdeiro da Pampa Pobre", "Engenheiros Do Hawaii", "Várias Variáveis", "Rock Brasileiro"),
    track("6", "Time", "Pink Floyd", "The Dark Side of the Moon", "Progressive Rock"),
    track("7", "Black Gives Way to Blue", "Alice In Chains", "Black Gives Way To Blue", "Grunge"),
]


class LibrarySearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = LibraryIndex(TRACKS)

    def ids(self, query, limit=3):
        return [r["id"] for r in self.index.search(query, limit)]

    def test_normalization(self):
        self.assertEqual(normalize("Ænima!"), "aenima")
        self.assertEqual(normalize("Várias  Variáveis"), "varias variaveis")
        self.assertEqual(normalize("AC/DC"), "ac dc")
        self.assertEqual(normalize("Guns N' Roses"), "guns n roses")

    def test_accents_typos_prefixes_and_compact_names(self):
        self.assertEqual(self.ids("varias variaveis")[0], "5")
        self.assertEqual(self.ids("metalica")[0], "4")  # erro de digitação
        self.assertEqual(self.ids("engenh")[0], "5")    # ainda digitando
        self.assertEqual(self.ids("acdc", 5), ["1", "2"])
        self.assertEqual(self.ids("aenima")[0], "3")
        self.assertEqual(self.ids("enima")[0], "3")

    def test_every_term_must_match_and_exact_title_wins(self):
        self.assertEqual(self.ids("pink floyd time"), ["6"])
        self.assertEqual(self.ids("pink floyd battery"), [])
        self.assertEqual(self.ids("back in black")[0], "1")

    def test_genre_search_and_reason(self):
        result = self.index.search("thrash", 5)
        self.assertEqual([r["id"] for r in result], ["4"])
        self.assertEqual(result[0]["motivo"], "gênero")

    def test_suggestions_group_artists_albums_and_songs(self):
        suggestions = self.index.suggest("ac")
        self.assertEqual(suggestions["artistas"][0], {"nome": "AC/DC", "faixas": 2})
        self.assertEqual(suggestions["albuns"][0]["titulo"], "Back In Black")
        self.assertEqual(suggestions["albuns"][0]["faixas"], 2)
        self.assertIn("1", [s["id"] for s in suggestions["musicas"]])
        self.assertEqual(self.index.suggest("   "), {"artistas": [], "albuns": [], "musicas": []})

    def test_artist_and_album_artist_count_once(self):
        index = LibraryIndex([track("1", "Song", "Kyuss", "Wretch", album_artist="Kyuss")])
        self.assertEqual(index.suggest("kyuss")["artistas"], [{"nome": "Kyuss", "faixas": 1}])

    def test_large_library_stays_fast(self):
        import time
        many = [track(str(i), f"Song {i}", f"Band {i % 300}", f"Album {i % 900}", "Rock") for i in range(3000)]
        index = LibraryIndex(many)
        start = time.perf_counter()
        index.suggest("band 12")
        index.search("albm 45")
        self.assertLess(time.perf_counter() - start, 1.5)


FAKE_WORKER = textwrap.dedent('''
    import json, sys, time
    calls = 0
    for line in sys.stdin:
        request = json.loads(line)
        calls += 1
        text = request["args"][0]
        if text == "lento":
            time.sleep(5)
        if text == "falha":
            print(json.dumps({"id": request["id"], "ok": False, "erro": "sem rede"}), flush=True)
            continue
        print("aviso solto de alguma biblioteca", flush=True)
        print(json.dumps({"id": request["id"] - 1, "ok": True, "dados": "atrasada"}), flush=True)
        print(json.dumps({"id": request["id"], "ok": True, "dados": {"termos": [text + " x"], "artistas": [{"nome": text.title()}], "chamadas": calls}}), flush=True)
''')


class GlobalSearchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        script = Path(self.temp.name) / "worker.py"
        script.write_text(FAKE_WORKER)
        self.search = GlobalSearch(lambda: [sys.executable, str(script)], timeout=1.0)

    def tearDown(self):
        self.search.close()
        self.temp.cleanup()

    def test_answers_skip_noise_and_late_replies_and_are_cached(self):
        first = self.search.suggest("Kyuss")
        self.assertEqual(first["termos"], ["Kyuss x"])
        self.assertEqual(self.search.suggest("  kyuss ")["chamadas"], 1)  # mesma consulta normalizada
        self.assertEqual(self.search.suggest("alice")["chamadas"], 2)    # o processo continua aberto
        self.assertEqual(self.search.suggest("k"), {"termos": [], "artistas": []})

    def test_errors_and_timeouts_recover(self):
        with self.assertRaisesRegex(RuntimeError, "sem rede"):
            self.search.suggest("falha")
        with self.assertRaisesRegex(RuntimeError, "demorou"):
            self.search.suggest("lento")
        self.assertIsNone(self.search.process)
        self.assertEqual(self.search.suggest("depois")["chamadas"], 1)  # um processo novo

    def test_close_during_pending_query(self):
        import threading
        self.search.timeout = 10.0
        process = (self.search.suggest("aquece"), self.search.process)[1]
        errors = []
        worker = threading.Thread(target=lambda: errors.append(self._capture(lambda: self.search.suggest("lento"))))
        worker.start()
        time.sleep(0.3)
        started = time.monotonic()
        self.search.close()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertTrue(process.stdin.closed and process.stdout.closed)

    @staticmethod
    def _capture(call):
        try:
            call()
        except Exception as error:
            return error


if __name__ == "__main__":
    unittest.main()
