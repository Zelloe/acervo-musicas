"""New assets load without restart while respecting the web directory boundary."""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from library_core import sha256
from api_contract import to_api
from server import App, Handler


class StaticHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "music"
        root.mkdir()
        self.assets = base / "assets"
        self.web = self.assets / "web"
        self.web.mkdir(parents=True)
        self.app = App(root, base / "dados")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.addCleanup(self.server.server_close)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.patch_base = patch("server.BASE", self.assets)
        self.patch_base.start()
        self.addCleanup(self.patch_base.stop)

    def stop(self):
        self.server.shutdown()
        self.worker.join()
        self.app.global_search.close()

    def get(self, path, status=200):
        try:
            response = urlopen(self.url + path, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            self.assertEqual(response.status, status, body)
            return body, response.headers

    def test_asset_created_after_server_start_is_available_with_correct_mime(self):
        self.get("/new_feature.js", status=404)
        (self.web / "new_feature.js").write_text('"use strict";')
        body, headers = self.get("/new_feature.js?v=2")
        self.assertEqual(body, b'"use strict";')
        self.assertIn("text/javascript", headers["Content-Type"])
        (self.web / "new-style.css").write_text("body{}")
        body, headers = self.get("/new-style.css")
        self.assertEqual(body, b"body{}")
        self.assertIn("text/css", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_traversal_subdirectories_unexpected_extensions_and_symlinks_are_blocked(self):
        secret = self.assets / "secret.js"
        secret.write_text("private contents")
        (self.web / "linked.js").symlink_to(secret)
        (self.web / "directory.js").mkdir()
        sub = self.web / "sub"
        sub.mkdir()
        (sub / "nested.js").write_text("nested")
        for path in ("/../secret.js", "/%2e%2e/secret.js", "/sub/nested.js", "/linked.js", "/directory.js", "/.hidden.js", "/UPPER.js", "/server.py", "/missing.css"):
            with self.subTest(path=path):
                body, _ = self.get(path, status=404)
                self.assertNotIn(b"private contents", body)
        moved = self.assets / "real-web"
        self.web.rename(moved)
        (moved / "normal.js").write_text("normal")
        self.web.symlink_to(moved, target_is_directory=True)
        self.get("/normal.js", status=404)

    def test_state_detects_server_code_change_from_the_loaded_version(self):
        source = self.assets / "server-source.py"
        source.write_text("loaded version")
        with patch("server.SERVER_SOURCE", source), patch("server.SERVER_CODE_HASH", sha256(source)):
            self.assertFalse(json.loads(self.get("/api/state")[0])['restart_required'])
            source.write_text("updated version")
            self.assertTrue(json.loads(self.get("/api/state")[0])['restart_required'])
            source.write_text("loaded version")
            self.assertFalse(json.loads(self.get("/api/state")[0])['restart_required'])
            source.unlink()
            self.assertTrue(json.loads(self.get("/api/state")[0])['restart_required'])


if __name__ == "__main__":
    unittest.main()
