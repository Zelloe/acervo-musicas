"""Choosing another music folder from the interface."""

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import acervo
from server import App, Handler


class LibraryOpenHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.first, self.second = self.base / "First", self.base / "Second"
        self.first.mkdir()
        self.second.mkdir()
        self.storage = self.base / "data"
        analyze = patch("server.App.analyze", return_value={})
        self.analyze = analyze.start()
        self.addCleanup(analyze.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.server.app = App(self.first, self.storage)
        self.token = self.server.app.token
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.thread.join()
        self.server.app.global_search.close()

    def post(self, route, data, status=200, token=None):
        headers = {"Content-Type": "application/json", "Origin": self.url, "X-Library-Token": token or self.token}
        try:
            response = urlopen(Request(self.url + route, json.dumps(data).encode(), headers), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result

    def test_listing_shows_only_real_visible_subfolders(self):
        for name in ("beta", "Alpha", ".hidden"):
            (self.first / name).mkdir()
        (self.first / "song.mp3").write_bytes(b"x")
        (self.first / "link").symlink_to(self.second)
        listing = self.post("/api/folders", {"path": str(self.first)})
        self.assertEqual([row["name"] for row in listing["folders"]], ["Alpha", "beta"])
        self.assertEqual((listing["path"], listing["parent"]), (str(self.first), str(self.base)))
        self.assertIsNone(self.post("/api/folders", {"path": "/"})["parent"])

    def test_invalid_paths_and_missing_token_are_refused(self):
        for value in ("relative/path", str(self.base / "missing"), str(self.first / "song"), None, "a\nb"):
            with self.subTest(value=value):
                self.post("/api/folders", {"path": value}, status=400)
                self.post("/api/library/open", {"folder": value}, status=400)
        self.post("/api/folders", {"path": str(self.first)}, status=403, token="wrong")
        self.post("/api/library/open", {"folder": str(self.second)}, status=403, token="wrong")
        self.assertEqual(self.server.app.root, self.first)

    def test_opening_a_folder_switches_library_keeps_token_and_scans_it(self):
        old = self.server.app
        old.fixed_folder = True
        old.worker and old.worker.join(5)
        self.assertEqual(self.post("/api/library/open", {"folder": str(self.second)})["library"], str(self.second))
        app = self.server.app
        self.assertIsNot(app, old)
        self.assertEqual((app.root, app.token, app.fixed_folder), (self.second, self.token, True))
        self.assertEqual(app.job["type"], "analysis")
        app.worker.join(5)
        self.analyze.assert_called_once()
        with urlopen(self.url + "/api/state", timeout=5) as response:
            state = json.loads(response.read())
        self.assertEqual((state["library"], state["fixed_folder"]), (str(self.second), True))

    def test_opening_the_current_folder_changes_nothing(self):
        old = self.server.app
        self.post("/api/library/open", {"folder": str(self.first)})
        self.assertIs(self.server.app, old)

    def test_work_on_the_library_blocks_the_switch_but_a_scan_does_not(self):
        app = self.server.app
        app.job = {"id": "x", "type": "apply", "status": "running", "message": "", "progress": None}
        self.post("/api/library/open", {"folder": str(self.second)}, status=400)
        app.job["type"] = "analysis"
        app.download_queue.append({"estado": "aguardando"})
        self.post("/api/library/open", {"folder": str(self.second)}, status=400)
        app.download_queue.clear()
        self.post("/api/library/open", {"folder": str(self.second)})
        self.assertEqual(self.server.app.root, self.second)
        app.job = None

    def test_folder_holding_the_program_data_is_refused_and_the_library_keeps_working(self):
        self.post("/api/library/open", {"folder": str(self.base)}, status=400)
        app = self.server.app
        self.assertEqual(app.root, self.first)
        self.assertFalse(app.closed)
        app.worker and app.worker.join(5)
        app.start("analysis", lambda: {})
        app.worker.join(5)

    def test_old_library_refuses_work_while_and_after_switching(self):
        old = self.server.app
        old.worker and old.worker.join(5)
        refused = []
        real = App

        def build(*args):
            for operation in (
                lambda: old.start("import", lambda: self.fail("wrote during the switch")),
                lambda: old.start_bulk_lyrics({}),
                lambda: old.change_protections({"ids": ["x"], "campos": ["genero"], "proteger": True}),
            ):
                with self.assertRaises(ValueError):
                    operation()
                refused.append(True)
            return real(*args)

        with patch("server.App", side_effect=build):
            self.post("/api/library/open", {"folder": str(self.second)})
        self.assertEqual(len(refused), 3)
        self.assertEqual(self.server.app.root, self.second)
        with self.assertRaises(ValueError):
            old.start("import", lambda: self.fail("wrote after the switch"))

    def test_only_an_explicit_folder_option_overrides_the_remembered_one(self):
        default = self.first
        for arguments, fixed in (
            ([], False),
            (["--folder", str(default)], True),
            (["--pasta", str(default)], True),
            (["--folder", str(self.second)], True),
        ):
            with (
                self.subTest(arguments=arguments),
                patch("sys.argv", ["acervo.py", "serve", *arguments]),
                patch("acervo.default_music_folder", return_value=default),
                patch("server.serve") as serve,
            ):
                acervo.main()
                self.assertIs(serve.call_args.args[4], fixed)
                self.assertEqual(serve.call_args.args[0], Path(arguments[1]) if arguments else default)


if __name__ == "__main__":
    unittest.main()
