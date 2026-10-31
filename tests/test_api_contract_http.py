"""English HTTP fields preserve legacy storage schemas and user metadata."""

from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from library_core import sha256
import protections
from server import App, Handler


class APIContractHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "Music"
        self.root.mkdir()
        self.path = self.root / "untouched.mp3"
        self.path.write_bytes(b"Temporary file; these tests never decode or modify audio.")
        self.original = sha256(self.path)
        self.app = App(self.root, base / "data")
        # Inventory data on disk keeps this schema for existing backups/plans.
        self.track = {"id": "fixture", "arquivo": str(self.path), "titulo": ["estado"],
                      "artista": ["artista"], "artista_album": [], "album": ["biblioteca"],
                      "genero": ["genero", "estudio"], "ano": [], "faixa": [], "disco": [],
                      "formato": "MP3", "duracao_segundos": 1, "capas": [], "campos_ausentes": ["ano"]}
        self.app.tracks = [self.track]
        self.app.by_id = {"fixture": self.track}
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
        self.app.global_search.close()

    def request(self, route, data=None, status=200):
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

    def test_english_fields_leave_metadata_and_inventory_unchanged(self):
        before = deepcopy(self.track)
        result = self.request("/api/track?id=fixture")
        self.assertEqual((result["title"], result["artist"], result["album"], result["genre"]),
                         (["estado"], ["artista"], ["biblioteca"], ["genero", "estudio"]))
        self.assertEqual(result["missing_fields"], ["year"])
        self.assertNotIn("arquivo", result)
        self.assertEqual(result["file"], str(self.path))
        self.assertEqual(self.track, before)
        self.app.plan = {"resumo": {"revisoes": 1, "consultas_musicbrainz": 2}}
        summary = self.request("/api/state")["summary"]
        self.assertEqual((summary["review_count"], summary["musicbrainz_requests"]), (1, 2))
        self.assertNotIn("revisoes", summary)
        self.assertEqual(sha256(self.path), self.original)

    def test_english_protection_request_keeps_saved_policy_readable_by_core(self):
        self.request("/api/protections", {"ids": ["fixture"], "fields": ["genre", "year"], "protect": True})
        details = self.request("/api/track?id=fixture")
        self.assertCountEqual(details["protected_fields"], ["genre", "year"])
        policy = protections.load(self.app.storage, self.root)
        self.assertEqual(protections.protected(policy, self.root, str(self.path)), {"genero", "ano"})
        saved = json.loads(protections.policy_path(self.app.storage, self.root).read_text())
        self.assertIn("biblioteca", saved)
        self.assertNotIn("library", saved)
        self.assertEqual(sha256(self.path), self.original)

    def test_provider_records_and_lyrics_text_are_not_reinterpreted(self):
        provider = {"title": "estado", "artista": "vendor-specific field", "status": "biblioteca"}
        batch = {"id": "fixture", "estado": "pronto", "faixas": [{"id": "row", "fonte": provider,
                 "metadados": {"titulo": ["titulo"], "genero": ["estudio"]}}]}
        before = deepcopy(batch)
        with patch.object(self.app.downloads, "load", return_value=batch):
            result = self.request("/api/downloads/batch?id=fixture")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["tracks"][0]["source"], provider)
        self.assertEqual(result["tracks"][0]["metadata"], {"title": ["titulo"], "genre": ["estudio"]})
        self.assertEqual(batch, before)
        lyrics = {"encontrada": True, "texto": "arquivo\nexecutando\ntitulo", "sincronizada": [], "fonte": "arquivo"}
        with patch.object(self.app, "lyrics", return_value=lyrics):
            self.assertEqual(self.request("/api/lyrics?id=fixture"),
                             {"found": True, "text": "arquivo\nexecutando\ntitulo", "synced": [], "source": "file"})

    def test_conflicting_request_aliases_are_rejected_before_policy_is_written(self):
        result = self.request("/api/protections", {"ids": ["fixture"], "fields": ["genre"],
                              "campos": ["ano"], "protect": True}, status=400)
        self.assertIn("error", result)
        self.assertFalse(protections.policy_path(self.app.storage, self.root).exists())
        self.assertEqual(sha256(self.path), self.original)


if __name__ == "__main__":
    unittest.main()
