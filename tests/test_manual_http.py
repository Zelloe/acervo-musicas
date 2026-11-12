"""Manual edits use real temporary audio, explicit previews and recoverable originals."""

import base64
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1, TXXX, USLT, delete

from acervo.library import protections
from acervo.library.artwork import decode_cover
from acervo.library.core import integrity, metadata_values, restore, sha256, write_validated
from acervo.library.manual import remove_track
from acervo.library.plan import create_plan
from acervo.web.api_contract import to_api
from acervo.web.server import App, Handler


@unittest.skipUnless(shutil.which("ffmpeg"), "Temporary audio and images require ffmpeg")
class ManualHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.TemporaryDirectory()
        cls.sample_root = Path(cls.samples.name)
        for ext in ("mp3", "flac"):
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=duration=0.1",
                    str(cls.sample_root / ("sample." + ext)),
                ],
                check=True,
            )
        delete(cls.sample_root / "sample.mp3")
        for ext in ("png", "jpg"):
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=32x32",
                    "-frames:v",
                    "1",
                    str(cls.sample_root / ("cover." + ext)),
                ],
                check=True,
            )

    @classmethod
    def tearDownClass(cls):
        cls.samples.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.storage = base / "Músicas", base / "dados"
        self.root.mkdir()
        self.music = self.root / "Arquivo sem tags.mp3"
        shutil.copy2(self.sample_root / "sample.mp3", self.music)
        self.flac = self.root / "Outra faixa.flac"
        shutil.copy2(self.sample_root / "sample.flac", self.flac)
        create_plan(self.root, self.storage / "relatorios/inicial", storage=self.storage)
        self.app = App(self.root, self.storage)
        self.ident = next(t["id"] for t in self.app.tracks if t["arquivo"] == str(self.music))
        self.flac_id = next(t["id"] for t in self.app.tracks if t["arquivo"] == str(self.flac))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.image = base64.b64encode((self.sample_root / "cover.png").read_bytes()).decode()

    def stop(self):
        if self.app.worker:
            self.app.worker.join(10)
        self.server.shutdown()
        self.worker.join()
        self.server.server_close()
        self.app.global_search.close()

    def request(self, route, data=None, status=200, **headers):
        defaults = {"Content-Type": "application/json", "Origin": self.url, "X-Library-Token": self.app.token}
        defaults.update(headers)
        request = Request(self.url + route, None if data is None else json.dumps(data).encode(), defaults)
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result

    def wait_job(self, status="completed"):
        self.app.worker.join(10)
        self.assertEqual(self.app.job["status"], status, self.app.job)
        return to_api(self.app.job.get("result", {}))

    def edit(self, fields, ident=None):
        editor = self.request("/api/tracks/editor?id=" + (ident or self.ident))
        return self.request(
            "/api/tracks/edit/preview",
            {"id": editor["id"], "fields": fields, "previous_sha256": editor["previous_sha256"]},
        )

    def save(self, preview, status="completed"):
        self.request("/api/tracks/save", {"preview": preview["preview"]}, status=202)
        return self.wait_job(status)

    def undo(self):
        history = self.request("/api/history")["runs"]
        self.request("/api/restore", {"id": history[0]["id"]}, status=202)
        return self.wait_job()

    def test_tagless_mp3_can_be_found_edited_cleared_and_restored_exactly(self):
        original, audio = sha256(self.music), integrity(self.music)["audio_sha256"]
        rows = self.request("/api/library?q=Arquivo%20sem%20tags")["tracks"]
        self.assertEqual([t["id"] for t in rows], [self.ident])
        self.assertEqual(rows[0]["title"], [])
        self.assertEqual(rows[0]["file"], str(self.music))
        preview = self.edit({"title": ["Título manual"], "artist": ["Artista"], "genre": ["Rock", "Blues"]})
        self.assertEqual(sha256(self.music), original)
        self.assertFalse((self.storage / "backups").exists())
        self.save(preview)
        self.assertEqual(metadata_values(self.music)["titulo"], ["Título manual"])
        self.assertEqual(integrity(self.music)["audio_sha256"], audio)
        self.request("/api/tracks/save", {"preview": preview["preview"]}, status=400)
        self.save(self.edit({"title": []}))
        self.assertEqual(metadata_values(self.music)["titulo"], [])
        self.undo()
        self.assertEqual(metadata_values(self.music)["titulo"], ["Título manual"])
        run = next(row for row in self.app.history() if row["estado"] != "restaurado")
        self.request("/api/restore", {"id": run["id"]}, status=202)
        self.wait_job()
        self.assertEqual(sha256(self.music), original)

    def test_edit_preserves_unapproved_id3_frames_and_version(self):
        for version in (3, 4):
            tags = ID3()
            for frame in (
                TIT2(encoding=1, text=["Old"]),
                TCON(encoding=1, text=["Rock"]),
                TXXX(encoding=1, desc="MusicBrainz Album Id", text=["keep"]),
                USLT(encoding=1, lang="por", text="Original lyrics"),
            ):
                tags.add(frame)
            tags.save(self.music, v2_version=version)
            original, before = sha256(self.music), integrity(self.music)
            self.save(self.edit({"title": ["New"]}))
            after = integrity(self.music)
            self.assertEqual(after["versao"], before["versao"])
            self.assertEqual(after["audio_sha256"], before["audio_sha256"])
            self.assertEqual(
                {k: v for k, v in after["tags"].items() if k != "TIT2"},
                {k: v for k, v in before["tags"].items() if k != "TIT2"},
            )
            self.undo()
            self.assertEqual(sha256(self.music), original)

    def test_png_and_jpeg_embed_without_changing_audio_or_tags_and_undo_exactly(self):
        for path, ident, ext in ((self.music, self.ident, "png"), (self.flac, self.flac_id, "jpg")):
            image = base64.b64encode((self.sample_root / ("cover." + ext)).read_bytes()).decode()
            original, before = sha256(path), integrity(path)
            preview = self.request("/api/tracks/cover/preview", {"id": ident, "image": image})
            self.assertEqual(preview["count"], 1)
            self.assertEqual(sha256(path), original)
            self.save(preview)
            after = integrity(path)
            self.assertEqual(after["audio_sha256"], before["audio_sha256"])
            self.assertEqual(
                metadata_values(path),
                {field: [] for field in metadata_values(path)}
                if path == self.music
                else metadata_values(self.sample_root / "sample.flac"),
            )
            self.assertTrue(self.request("/api/track?id=" + ident)["covers"])
            self.request("/api/tracks/cover/preview", {"id": ident, "image": image}, status=400)
            manifests = list((self.storage / "backups").glob("*/manifesto.json"))
            self.assertTrue(all(image not in p.read_text(encoding="utf-8") for p in manifests))
            self.undo()
            self.assertEqual(sha256(path), original)

    def test_album_cover_fills_only_missing_images_in_same_album_and_artist(self):
        self.save(self.edit({"album": ["Disco"], "artist": ["Banda"]}))
        self.save(self.edit({"album": ["Disco"], "artist": ["Banda"]}, self.flac_id))
        other = self.root / "different artist.mp3"
        shutil.copy2(self.sample_root / "sample.mp3", other)
        tags = ID3()
        tags.add(TPE1(encoding=3, text=["Outra banda"]))
        tags.add(TALB(encoding=3, text=["Disco"]))
        tags.save(other)
        self.app.refresh_downloaded([{"estado": "baixado", "arquivo": str(other)}])
        original = sha256(other)
        first = self.request("/api/tracks/cover/preview", {"id": self.ident, "image": self.image})
        self.save(first)
        covered = sha256(self.music)
        preview = self.request("/api/tracks/cover/preview", {"id": self.ident, "image": self.image, "album": True})
        self.assertEqual((preview["count"], preview["skipped"]), (1, 1))
        self.save(preview)
        self.assertEqual(sha256(self.music), covered)
        self.assertEqual(sha256(other), original)
        self.assertTrue(FLAC(self.flac).pictures)

    def test_delete_is_previewed_backed_up_hidden_on_restart_and_restored(self):
        original, untouched = sha256(self.music), sha256(self.flac)
        preview = self.request("/api/tracks/delete/preview", {"id": self.ident})
        self.assertEqual(sha256(self.music), original)
        self.save(preview)
        self.assertFalse(self.music.exists())
        self.assertNotIn(self.ident, self.app.by_id)
        self.assertEqual(sha256(self.flac), untouched)
        run = self.request("/api/history")["runs"][0]
        self.assertEqual(run["type"], "deletion")
        self.save(self.edit({"title": ["Later edit"]}, self.flac_id))
        restarted = App(self.root, self.storage)
        self.addCleanup(restarted.global_search.close)
        self.assertNotIn(self.ident, restarted.by_id)
        self.assertIsNone(restarted.error)
        self.app.proposals()  # Old analysis must not refer to the removed inventory row.
        self.request("/api/restore", {"id": run["id"]}, status=202)
        self.wait_job()
        self.assertEqual(sha256(self.music), original)
        self.assertIn(self.ident, self.app.by_id)

    def test_delete_restore_refuses_destination_and_damaged_backup(self):
        preview = self.request("/api/tracks/delete/preview", {"id": self.ident})
        result = self.save(preview)
        manifest_path = Path(result["manifest"])
        self.music.write_bytes(b"new occupant")
        with self.assertRaises(ValueError):
            restore(manifest_path, self.storage)
        self.assertEqual(self.music.read_bytes(), b"new occupant")
        self.music.unlink()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        backup = manifest_path.parent / manifest["arquivos"][0]["backup"]
        original = backup.read_bytes()
        backup.write_bytes(b"damaged")
        with self.assertRaises(ValueError):
            restore(manifest_path, self.storage)
        self.assertFalse(self.music.exists())
        backup.write_bytes(original)
        restore(manifest_path, self.storage)
        self.assertEqual(self.music.read_bytes(), original)
        self.assertEqual(restore(manifest_path, self.storage)["arquivos_restaurados"], 0)

    def test_stale_edit_and_delete_previews_refuse_changed_files(self):
        for kind in ("edit", "delete"):
            preview = (
                self.edit({"title": ["New"]})
                if kind == "edit"
                else self.request("/api/tracks/delete/preview", {"id": self.ident})
            )
            with self.music.open("ab") as stream:
                stream.write(b"later edit")
            changed = sha256(self.music)
            self.save(preview, "error")
            self.assertEqual(sha256(self.music), changed)
        editor = self.request("/api/tracks/editor?id=" + self.ident)
        with self.music.open("ab") as stream:
            stream.write(b"x")
        self.request(
            "/api/tracks/edit/preview",
            {"id": self.ident, "fields": {"title": ["New"]}, "previous_sha256": editor["previous_sha256"]},
            status=400,
        )

    def test_cover_batch_is_rejected_before_writes_when_a_member_changed(self):
        for ident in (self.ident, self.flac_id):
            self.save(self.edit({"album": ["Same"], "artist": ["Same"]}, ident))
        preview = self.request("/api/tracks/cover/preview", {"id": self.ident, "image": self.image, "album": True})
        original = sha256(self.music)
        with self.flac.open("ab") as stream:
            stream.write(b"changed")
        self.save(preview, "error")
        self.assertEqual(sha256(self.music), original)

    def test_protected_fields_and_corrupt_policy_block_writes(self):
        protections.protect(self.storage, self.root, [str(self.music)], ["titulo"])
        editor = self.request("/api/tracks/editor?id=" + self.ident)
        self.assertEqual(editor["protected_fields"], ["title"])
        self.request(
            "/api/tracks/edit/preview",
            {"id": self.ident, "fields": {"title": ["No"]}, "previous_sha256": editor["previous_sha256"]},
            status=400,
        )
        preview = self.edit({"artist": ["Allowed"]})
        protections.policy_path(self.storage, self.root).write_text("invalid", encoding="utf-8")
        self.request("/api/tracks/save", {"preview": preview["preview"]}, status=400)

    def test_invalid_inputs_paths_tokens_and_origin_are_refused(self):
        original = sha256(self.music)
        for data in ({}, {"id": "missing"}, {"id": self.ident, "file": str(self.flac)}, {"id": [self.ident]}):
            self.request("/api/tracks/delete/preview", data, status=400)
        self.request("/api/tracks/delete/preview", {"id": self.ident}, status=403, **{"X-Library-Token": "bad"})
        self.request("/api/tracks/delete/preview", {"id": self.ident}, status=403, Origin="http://evil.invalid")
        self.request("/api/tracks/save", {"preview": "unknown"}, status=400)
        preview = self.edit({"title": ["New"]})
        self.app.manual_previews[preview["preview"]]["created"] -= 901
        self.request("/api/tracks/save", {"preview": preview["preview"]}, status=400)
        for fields in ({"title": "bad"}, {"filename": ["x"]}, {"title": ["\0"]}, {}):
            editor = self.request("/api/tracks/editor?id=" + self.ident)
            self.request(
                "/api/tracks/edit/preview",
                {"id": self.ident, "fields": fields, "previous_sha256": editor["previous_sha256"]},
                status=400,
            )
        link = self.root / "hardlink.mp3"
        os.link(self.music, link)
        self.request("/api/tracks/delete/preview", {"id": self.ident}, status=400)
        link.unlink()
        self.music.unlink()
        self.music.symlink_to(self.sample_root / "sample.mp3")
        self.request("/api/tracks/delete/preview", {"id": self.ident}, status=400)
        self.assertEqual(sha256(self.music), original)

    def test_invalid_images_and_tagless_album_scope_are_refused(self):
        for image in ("", "bad", [], base64.b64encode(b"not an image").decode()):
            self.request("/api/tracks/cover/preview", {"id": self.ident, "image": image}, status=400)
        png = (self.sample_root / "cover.png").read_bytes()
        for data in (png[:33], png[:16] + struct.pack(">II", 100000, 100000) + png[24:]):
            with self.assertRaises(ValueError):
                decode_cover(base64.b64encode(data).decode())
        self.request("/api/tracks/cover/preview", {"id": self.ident, "image": self.image, "album": True}, status=400)
        self.request("/api/tracks/cover/preview", {"id": self.ident, "image": self.image, "album": "yes"}, status=400)

    def test_failed_backup_never_removes_the_original(self):
        original = sha256(self.music)
        with patch("acervo.library.manual.durable_copy", side_effect=OSError("disk error")):
            with self.assertRaises(RuntimeError):
                remove_track(self.root, self.storage, str(self.music), original)
        self.assertEqual(sha256(self.music), original)

    def test_stale_album_membership_and_invalid_audio_are_refused(self):
        for ident in (self.ident, self.flac_id):
            self.save(self.edit({"album": ["Same"], "artist": ["Same"]}, ident))
        audio = FLAC(self.flac)
        audio["artist"] = ["Different artist"]
        audio.save()
        original = sha256(self.music)
        self.request("/api/tracks/cover/preview", {"id": self.ident, "image": self.image, "album": True}, status=400)
        self.assertEqual(sha256(self.music), original)
        self.music.write_bytes(b"invalid audio")
        self.request("/api/tracks/editor?id=" + self.ident, status=400)

    def test_interrupted_restore_can_finish_without_overwriting_published_original(self):
        preview = self.request("/api/tracks/delete/preview", {"id": self.ident})
        result = self.save(preview)
        path = Path(result["manifest"])
        manifest = json.loads(path.read_text(encoding="utf-8"))
        backup = path.parent / manifest["arquivos"][0]["backup"]
        shutil.copy2(backup, self.music)
        before = self.music.stat()
        self.assertEqual(restore(path, self.storage)["arquivos_restaurados"], 0)
        self.assertEqual(self.music.stat().st_ino, before.st_ino)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["estado"], "restaurado")

    def test_flac_with_no_comment_block_accepts_its_first_tags(self):
        # Remove the entire Vorbis comment block, retaining STREAMINFO, padding and audio.
        data = self.flac.read_bytes()
        blocks, offset = [], 4
        while True:
            header = data[offset : offset + 4]
            size = int.from_bytes(header[1:], "big")
            block = data[offset : offset + 4 + size]
            if header[0] & 127 != 4:
                blocks.append(block)
            offset += 4 + size
            if header[0] & 128:
                break
        self.flac.write_bytes(
            b"fLaC"
            + b"".join(
                bytes([(block[0] & 127) | (128 if i == len(blocks) - 1 else 0)]) + block[1:]
                for i, block in enumerate(blocks)
            )
            + data[offset:]
        )
        self.assertIsNone(FLAC(self.flac).tags)
        before = integrity(self.flac)["audio_sha256"]
        write_validated(self.flac, {"titulo": {"atual": [], "proposto": ["First tag"]}})
        self.assertEqual(metadata_values(self.flac)["titulo"], ["First tag"])
        self.assertEqual(integrity(self.flac)["audio_sha256"], before)
