"""Review and save lyrics with backups and protection against stale previews."""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1, USLT

from library_core import integrity, sha256
from library_plan import create_plan
from api_contract import to_api
from server import App, Handler


@unittest.skipUnless(shutil.which("ffmpeg"), "Tests generate temporary audio with ffmpeg")
class SaveLyricsHTTPTest(unittest.TestCase):
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
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "Coleção"
        self.root.mkdir()
        self.music = self.root / "Faixa.mp3"
        shutil.copy(self.sample, self.music)
        tags = ID3()
        for frame in (TIT2(encoding=3, text=["Título preservado"]), TPE1(encoding=3, text=["Banda fictícia"]), TALB(encoding=3, text=["Disco"]), TCON(encoding=3, text=["Rock"]), USLT(encoding=3, lang="por", desc="", text="Letra original fictícia")):
            tags.add(frame)
        tags.save(self.music, v2_version=3)
        self.original = sha256(self.music)
        self.audio_hash = integrity(self.music)["audio_sha256"]
        self.storage = base / "dados"
        create_plan(self.root, self.storage / "relatorios/inicial", storage=self.storage)
        self.app = App(self.root, self.storage)
        self.ident = self.app.tracks[0]["id"]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.addCleanup(self.server.server_close)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.worker.join()
        self.app.global_search.close()

    def request(self, route, data=None, status=200, **headers):
        defaults = {"Content-Type": "application/json", "Origin": self.url, "X-Acervo-Language": "pt-BR", "X-Library-Token": self.app.token}
        defaults.update(headers)
        request = Request(self.url + route, None if data is None else json.dumps(to_api(data)).encode(), defaults)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result

    def preview(self, text="Letra nova fictícia"):
        return self.request("/api/lyrics/preview", {"id": self.ident, "texto": text})

    def wait_job(self, expected="completed"):
        self.app.worker.join(5)
        self.assertEqual(self.app.job['status'], expected, self.app.job)
        return to_api(self.app.job.get('result', {}))

    def test_review_save_and_history_restore_keep_audio_and_other_tags(self):
        preview = self.preview()
        self.assertEqual(preview['current'], ["Letra original fictícia"])
        self.assertEqual(sha256(self.music), self.original)
        self.assertFalse((self.storage / "backups").exists())
        revision = self.app.library_revision
        self.request("/api/lyrics/save", {"previa": preview['preview']}, status=202)
        result = self.wait_job()
        self.assertEqual(result['lyrics_track_id'], self.ident)
        self.assertNotEqual(self.app.library_revision, revision)
        self.assertEqual(self.request("/api/lyrics?id=" + self.ident)['text'], "Letra nova fictícia")
        self.assertEqual(integrity(self.music)["audio_sha256"], self.audio_hash)
        self.assertEqual(ID3(self.music)["TIT2"].text, ["Título preservado"])
        history = self.request("/api/history")['runs']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['type'], "downloads")
        manifest = json.loads(Path(result['manifest']).read_text())
        backup = Path(result['manifest']).parent / manifest["arquivos"][0]["backup"]
        self.assertEqual(sha256(backup), self.original)
        self.request("/api/lyrics/save", {"previa": preview['preview']}, status=400)
        self.request("/api/restore", {"id": history[0]["id"]}, status=202)
        self.wait_job()
        self.assertEqual(sha256(self.music), self.original)

    def test_file_changed_after_preview_is_preserved_and_save_is_refused(self):
        preview = self.preview()
        tags = ID3(self.music)
        tags["TIT2"] = TIT2(encoding=3, text=["Edição externa posterior"])
        tags.save(self.music, v2_version=3)
        external = sha256(self.music)
        self.request("/api/lyrics/save", {"previa": preview['preview']}, status=202)
        self.wait_job("error")
        self.assertIn("mudou", self.app.job['message'])
        self.assertEqual(sha256(self.music), external)
        self.assertEqual(ID3(self.music).getall("USLT")[0].text, "Letra original fictícia")
        self.assertFalse((self.storage / "backups").exists())

    def test_invalid_inputs_missing_authentication_and_expired_preview_do_not_write(self):
        valid = {"id": self.ident, "texto": "Letra nova fictícia"}
        for payload in ({**valid, "arquivo": str(self.music)}, {**valid, "id": "fake"}, {**valid, "texto": ""}, {**valid, "texto": []}, {**valid, "texto": "[00:01.00]verso"}, {**valid, "texto": "a\x00b"}, {**valid, "texto": "x" * 100001}):
            self.request("/api/lyrics/preview", payload, status=400)
        for headers in ({"X-Library-Token": "wrong"}, {"Origin": "http://evil.invalid"}, {"Host": "evil.invalid"}):
            self.request("/api/lyrics/preview", valid, status=403, **headers)
            self.request("/api/lyrics/save", {"previa": "fake"}, status=403, **headers)
        preview = self.preview()
        self.request("/api/lyrics/save", {"previa": preview['preview'], "texto": "alterado no navegador"}, status=400)
        self.app.lyrics_previews[preview['preview']]["criado"] = time.monotonic() - 901
        self.request("/api/lyrics/save", {"previa": preview['preview']}, status=400)
        self.assertEqual(sha256(self.music), self.original)
        self.assertIsNone(self.app.job)

    def test_busy_operation_and_corrupt_policy_prevent_save(self):
        preview = self.preview()
        self.app.job = {"status": "running", "type": "apply"}
        self.request("/api/lyrics/save", {"previa": preview['preview']}, status=400)
        self.app.job = None
        import protections
        policy = protections.policy_path(self.storage, self.root)
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("invalid json")
        self.request("/api/lyrics/save", {"previa": preview['preview']}, status=400)
        self.assertEqual(sha256(self.music), self.original)


if __name__ == "__main__":
    unittest.main()
