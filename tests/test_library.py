import csv
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, APIC, TALB, TCON, TIT2, TPE1, TPOS, TRCK, TXXX, TYER

from library_core import apply_plan, integrity, restore, sha256, write_validated
from library_plan import create_plan, propose, rules, BASE
from library_scan import read_track


@unittest.skipUnless(shutil.which("ffmpeg"), "Os testes de integração usam ffmpeg para gerar áudio temporário")
class LibraryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.TemporaryDirectory()
        cls.sample_root = Path(cls.samples.name)
        for ext, codec in (("mp3", "libmp3lame"), ("flac", "flac")):
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1", "-c:a", codec, str(cls.sample_root / ("sample." + ext))], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.samples.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lib = self.root / "musicas"
        self.lib.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def music(self, name="teste.mp3", version=3):
        path = self.lib / name
        shutil.copy2(self.sample_root / ("sample" + path.suffix), path)
        if path.suffix == ".mp3":
            tags = ID3()
            for frame in [TIT2(encoding=1, text=["Back In Black"]), TPE1(encoding=1, text=["AC/DC"]), TALB(encoding=1, text=["Back In Black"]), TRCK(encoding=1, text=["6"]), TPOS(encoding=1, text=["1"]), TYER(encoding=1, text=["1980"]), TXXX(encoding=1, desc="REPLAYGAIN_TRACK_GAIN", text=["-5 dB"])]:
                tags.add(frame)
            for desc in ("", "Cover", "Cover (front)"):
                tags.add(APIC(encoding=1, mime="image/png", type=3, desc=desc, data=b"binary-cover-test"))
            tags.save(path, v2_version=version)
        else:
            audio = FLAC(path)
            audio["title"] = ["Back In Black"]
            audio["artist"] = ["AC/DC"]
            audio["album"] = ["Back In Black"]
            audio["REPLAYGAIN_TRACK_GAIN"] = ["-5 dB"]
            cover = Picture()
            cover.mime = "image/png"
            cover.type = 3
            cover.data = b"binary-cover-test"
            audio.add_picture(cover)
            audio.save()
        return path

    def plan(self):
        result = create_plan(self.lib, self.root / "plano", normalize_albums=True)
        return Path(result["plano"])

    def approvals(self, plan):
        path = plan.parent / "alteracoes.csv"
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames
            rows = list(reader)
        for row in rows:
            row["aprovar"] = "sim"
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_mp3_versions_cover_frames_and_id3v1_preserved(self):
        for version in (3, 4):
            with self.subTest(version=version):
                path = self.music(f"v{version}.mp3", version)
                with path.open("ab") as f:
                    f.write(b"TAG" + bytes(range(125)))
                before = integrity(path)
                after = write_validated(path, {"genero": {"atual": read_track(path)["genero"], "proposto": ["Hard Rock"]}})
                self.assertEqual(before["audio_sha256"], after["audio_sha256"])
                self.assertEqual(before["versao"], after["versao"])
                self.assertEqual(len(ID3(path).getall("APIC")), 3)
                for key, value in before["tags"].items():
                    if key != "TCON":
                        self.assertEqual(value, after["tags"][key])

    def test_flac_blocks_and_comments_preserved(self):
        path = self.music("teste.flac")
        before = integrity(path)
        after = write_validated(path, {"genero": {"atual": [], "proposto": ["Hard Rock"]}, "album": {"atual": ["Back In Black"], "proposto": ["Album de teste"]}})
        self.assertEqual(before["audio_sha256"], after["audio_sha256"])
        self.assertEqual(before["blocos"], after["blocos"])
        self.assertEqual(FLAC(path)["BIBLIOTECA_ALBUM_ANTERIOR"], ["Back In Black"])

    def test_approval_is_required(self):
        path = self.music()
        before = sha256(path)
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, "No rows approved"):
            apply_plan(plan, plan.parent / "alteracoes.csv", self.root / "backups")
        self.assertEqual(sha256(path), before)
        self.assertFalse((self.root / "backups").exists())

    def test_full_backup_restore_is_exact_and_idempotent(self):
        files = [self.music(), self.music("teste.flac")]
        before = {str(p): sha256(p) for p in files}
        plan = self.plan()
        result = apply_plan(plan, self.approvals(plan), self.root / "backups")
        self.assertTrue(all(read_track(p)["genero"] == ["Hard Rock"] for p in files))
        manifest = Path(result["manifesto"])
        self.assertEqual(restore(manifest)["arquivos_restaurados"], 2)
        self.assertEqual({str(p): sha256(p) for p in files}, before)
        self.assertEqual(restore(manifest)["arquivos_restaurados"], 0)

    def test_stale_plan_refused(self):
        path = self.music()
        plan = self.plan()
        with path.open("ab") as f:
            f.write(b"edited")
        with self.assertRaisesRegex(ValueError, "changed"):
            apply_plan(plan, self.approvals(plan), self.root / "backups")

    def test_restore_preserves_later_edits(self):
        path = self.music()
        plan = self.plan()
        result = apply_plan(plan, self.approvals(plan), self.root / "backups")
        with path.open("ab") as f:
            f.write(b"later-edit")
        edited = sha256(path)
        with self.assertRaisesRegex(ValueError, "later edits"):
            restore(Path(result["manifesto"]))
        self.assertEqual(sha256(path), edited)

    def test_corrupt_backup_refused(self):
        path = self.music()
        plan = self.plan()
        result = apply_plan(plan, self.approvals(plan), self.root / "backups")
        manifest = Path(result["manifesto"])
        data = json.loads(manifest.read_text())
        backup = manifest.parent / data["arquivos"][0]["backup"]
        backup.write_bytes(b"corrupt")
        current = sha256(path)
        with self.assertRaisesRegex(ValueError, "Damaged"):
            restore(manifest)
        self.assertEqual(sha256(path), current)

    def test_unapproved_tag_change_stops_publish(self):
        path = self.music()
        before = sha256(path)
        plan = self.plan()
        original_save = ID3.save
        def broken_save(tags, target, **kwargs):
            original_save(tags, target, **kwargs)
            altered = ID3(target, translate=False)
            altered.add(TIT2(encoding=1, text=["UNAPPROVED TITLE"]))
            original_save(altered, target, v2_version=3, v1=0)
        with patch.object(ID3, "save", broken_save):
            with self.assertRaisesRegex(RuntimeError, "tag TIT2"):
                apply_plan(plan, self.approvals(plan), self.root / "backups")
        self.assertEqual(sha256(path), before)
        self.assertEqual(list(self.lib.glob(".biblioteca-*")), [])

    def test_album_identity_and_alternative_versions(self):
        path = self.music()
        tags = ID3(path, translate=False)
        tags.add(TPE1(encoding=1, text=["Metallica"]))
        tags.add(TALB(encoding=1, text=["…And Justice for All (Remastered)"]))
        tags.add(TIT2(encoding=1, text=["One"]))
        tags.add(TRCK(encoding=1, text=["4"]))
        tags.save(path, v2_version=3)
        albums = rules(BASE / "album_rules.csv")
        changes, _ = propose(read_track(path), [], albums)
        self.assertTrue(any(c["campo"] == "album" for c in changes))
        for title, disc in (("One - Live", "1"), ("One - Demo", "1"), ("One", "2"), ("Another Song", "1")):
            tags.add(TIT2(encoding=1, text=[title]))
            tags.add(TPOS(encoding=1, text=[disc]))
            tags.save(path, v2_version=3)
            changes, _ = propose(read_track(path), [], albums)
            self.assertFalse(any(c["campo"] == "album" for c in changes))
        tags.add(TIT2(encoding=1, text=["To Live is to Die - Remastered"]))
        tags.add(TPOS(encoding=1, text=["1"]))
        tags.add(TRCK(encoding=1, text=["8"]))
        tags.save(path, v2_version=3)
        changes, _ = propose(read_track(path), [], albums)
        self.assertTrue(any(c["campo"] == "album" for c in changes))

    def test_existing_genre_is_not_overwritten(self):
        path = self.music()
        tags = ID3(path, translate=False)
        tags.add(TCON(encoding=1, text=["Custom genre"]))
        tags.save(path, v2_version=3)
        result = create_plan(self.lib, self.root / "existing")
        self.assertEqual(result["resumo"]["alteracoes_propostas"], 0)

    def test_tampered_plan_and_csv_refused(self):
        self.music()
        plan = self.plan()
        approvals = self.approvals(plan)
        text = approvals.read_text(encoding="utf-8-sig").replace("Hard Rock", "WRONG")
        approvals.write_text(text, encoding="utf-8-sig")
        with self.assertRaisesRegex(ValueError, "Edit only"):
            apply_plan(plan, approvals, self.root / "backups")
        plan.write_text(plan.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "plan was modified"):
            apply_plan(plan, approvals, self.root / "backups")

    def test_interrupted_batch_can_be_restored(self):
        files = [self.music("a.mp3"), self.music("b.mp3")]
        before = {str(p): sha256(p) for p in files}
        plan = self.plan()
        import library_core
        original_replace = library_core.os.replace
        def fail_second(source, target):
            if Path(target) == files[1]:
                raise OSError("interrupção simulada")
            return original_replace(source, target)
        with patch("library_core.os.replace", fail_second):
            with self.assertRaisesRegex(RuntimeError, "interrupção simulada"):
                apply_plan(plan, self.approvals(plan), self.root / "backups")
        manifest = next((self.root / "backups").glob("*/manifesto.json"))
        self.assertEqual(json.loads(manifest.read_text())["estado"], "interrompido")
        restore(manifest)
        self.assertEqual({str(p): sha256(p) for p in files}, before)

    def test_http_approval_restore_and_origin_validation(self):
        from http.server import ThreadingHTTPServer
        from server import App, Handler
        files = [self.music(), self.music("teste.flac")]
        before = {str(p): sha256(p) for p in files}
        storage = self.root / "aplicativo"
        create_plan(self.lib, storage / "relatorios/inicial", True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.app = App(self.lib, storage)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        def post(route, data, valid=True):
            headers = {"Content-Type": "application/json", "Origin": base, "X-Library-Token": server.app.token if valid else "wrong"}
            with urlopen(Request(base + route, json.dumps(data).encode(), headers)) as response:
                return response.status
        def wait_job():
            deadline = time.monotonic() + 5
            while server.app.job["status"] == "running" and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(server.app.job["status"], "completed", server.app.job)
        try:
            with self.assertRaises(HTTPError) as rejected:
                post("/api/apply", {"ids": []}, valid=False)
            self.assertEqual(rejected.exception.code, 403)
            rejected.exception.close()
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(base + "/api/state", headers={"Host": "evil.invalid"}))
            self.assertEqual(rejected.exception.code, 403)
            rejected.exception.close()
            ident = server.app.tracks[0]["id"]
            with urlopen(Request(base + "/api/audio?id=" + ident, headers={"Range": "bytes=0-9"})) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(len(response.read()), 10)
            ids = [p["id"] for p in server.app.proposals()]
            self.assertEqual(post("/api/apply", {"ids": ids}), 202)
            wait_job()
            self.assertEqual(len(server.app.proposals()), 0)
            history = server.app.history()
            self.assertEqual(len(history), 1)
            self.assertEqual(post("/api/restore", {"id": history[0]["id"]}), 202)
            wait_job()
            self.assertEqual({str(p): sha256(p) for p in files}, before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class MusicBrainzTest(unittest.TestCase):
    def test_homonyms_are_not_chosen(self):
        from musicbrainz_client import MusicBrainz
        with tempfile.TemporaryDirectory() as folder:
            client = MusicBrainz(Path(folder), "unit-tests@example.invalid")
            with patch.object(client, "get", return_value={"artists": [{"name": "Death", "score": "100", "id": "a"}, {"name": "Death", "score": "100", "id": "b"}]}):
                self.assertIsNone(client.artist("Death"))

    def test_specific_genre_and_unconfirmed_album_needs_review(self):
        from musicbrainz_client import MusicBrainz
        self.assertEqual(MusicBrainz.choose_genre({"genres": [{"name": "rock", "count": 20}, {"name": "progressive rock", "count": 5}]}), "Progressive Rock")
        self.assertIsNone(MusicBrainz.choose_genre({"genres": [{"name": "wrong", "count": -1}]}))
        with tempfile.TemporaryDirectory() as folder:
            client = MusicBrainz(Path(folder), "unit-tests@example.invalid")
            with patch.object(client, "get", side_effect=[{"artists": [{"name": "Pink Floyd", "score": "100", "id": "artist"}]}, {"release-groups": []}]) as mock:
                result = client.genre("Pink Floyd", "Unknown album")
                self.assertNotIn("genero", result)
                self.assertIn("revisao", result)
                client.genre("Pink Floyd", "Unknown album")
                self.assertEqual(mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
