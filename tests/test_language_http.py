"""Request languages are explicit and stay isolated across requests and jobs."""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from messages import _, current_language
from server import App, Handler


class LanguageHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "Music"
        root.mkdir()
        self.app = App(root, base / "data")
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
        if self.app.worker:
            self.app.worker.join(5)
        self.app.global_search.close()

    def request(self, route="/api/lyrics?id=missing", data=None, status=400, **extra):
        headers = {"Origin": self.url, "Content-Type": "application/json", "X-Library-Token": self.app.token, **extra}
        request = Request(self.url + route, None if data is None else json.dumps(data).encode(), headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result

    def test_language_header_and_accept_language_fallback_do_not_leak(self):
        for headers, expected in (
            ({}, "Song not found"),
            ({"X-Acervo-Language": "pt-BR"}, "Música não encontrada"),
            ({"X-Acervo-Language": "en", "Accept-Language": "pt-BR"}, "Song not found"),
            ({"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}, "Música não encontrada"),
            ({"X-Acervo-Language": "unsupported"}, "Song not found"),
            ({}, "Song not found"),
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(**headers), {"error": expected})
        self.assertEqual(current_language(), "en")

    def test_decoding_errors_use_the_current_request_language(self):
        payload = {"ids": [], "fields": ["genre"], "campos": ["ano"], "protect": True}
        self.assertEqual(self.request("/api/protections", payload), {"error": "Duplicate request fields"})
        self.assertEqual(self.request("/api/protections", payload, **{"X-Acervo-Language": "pt-BR"}),
                         {"error": "Campos repetidos na requisição"})
        self.assertFalse((self.app.storage / "protecoes").exists())

    def test_job_keeps_its_language_while_another_request_uses_portuguese(self):
        entered, release = threading.Event(), threading.Event()
        observed = []
        def analyze(*args):
            observed.append(current_language())
            entered.set()
            release.wait(5)
            observed.append(current_language())
            return {"language": current_language()}
        with patch.object(self.app, "analyze", side_effect=analyze):
            self.request("/api/analyze", {}, status=202, **{"X-Acervo-Language": "en"})
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.request(**{"X-Acervo-Language": "pt-BR"})["error"], "Música não encontrada")
            finally:
                release.set()
                self.app.worker.join(5)
        self.assertEqual(observed, ["en", "en"])
        self.assertEqual(self.app.job["message"], "Operation completed")
        self.assertEqual(self.app.job["result"], {"language": "en"})

    def test_job_failure_is_translated_inside_the_worker(self):
        def fail(*args):
            raise ValueError(_("Song not found"))
        with patch.object(self.app, "analyze", side_effect=fail):
            for code, expected in (("pt-BR", "Música não encontrada"), ("en", "Song not found")):
                self.request("/api/analyze", {}, status=202, **{"X-Acervo-Language": code})
                self.app.worker.join(5)
                self.assertEqual((self.app.job["status"], self.app.job["message"]), ("error", expected))
        self.assertEqual(current_language(), "en")


if __name__ == "__main__":
    unittest.main()
