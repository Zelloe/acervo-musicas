"""Album-wide edits require an exact preview and preserve every unapproved value."""

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCON, TIT2, TPE1, TPE2, TRCK, TXXX, USLT

from acervo.app import App
from acervo.library import protections
from acervo.library.core import integrity, metadata_values, restore, sha256
from acervo.library.plan import create_plan
from acervo.web.api_contract import to_api
from acervo.web.server import Handler


@unittest.skipUnless(shutil.which("ffmpeg"), "Temporary fixtures require ffmpeg")
class AlbumEditHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.TemporaryDirectory()
        cls.sample_root = Path(cls.samples.name)
        for extension in ("mp3", "flac"):
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=duration=0.12",
                    str(cls.sample_root / ("sample." + extension)),
                ],
                check=True,
            )
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
                str(cls.sample_root / "cover.png"),
            ],
            check=True,
        )
        cls.cover = (cls.sample_root / "cover.png").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.samples.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.storage = base / "Music", base / "data"
        self.root.mkdir()
        self.paths = [self.root / "01.mp3", self.root / "02.flac", self.root / "Other.mp3", self.root / "Unknown.mp3"]
        for index, path in enumerate(self.paths):
            shutil.copy(self.sample_root / ("sample" + path.suffix), path)
            if index == 3:
                ID3(path).delete(path)
                continue
            values = {
                "title": ["Complete title (Live)" if index == 0 else "Another song"],
                "artist": ["Singer A" if index == 0 else "Singer B"],
                "albumartist": ["Various Artists" if index < 2 else "Another band"],
                "album": ["Same album title"],
                "tracknumber": [str(index + 1)],
            }
            if path.suffix == ".mp3":
                tags = ID3(path)
                for field, frame in (
                    ("title", TIT2),
                    ("artist", TPE1),
                    ("albumartist", TPE2),
                    ("album", TALB),
                    ("tracknumber", TRCK),
                ):
                    tags.add(frame(encoding=3, text=values[field]))
                tags.add(TCON(encoding=3, text=["Existing genre"]))
                tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=["keep-id"]))
                tags.add(USLT(encoding=3, lang="eng", text="Keep original lyrics"))
                tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Original cover", data=self.cover))
                tags.save(path, v2_version=3)
            else:
                audio = FLAC(path)
                audio.update(values)
                audio["lyrics"] = ["Keep FLAC lyrics"]
                audio["musicbrainz_albumid"] = ["keep-flac-id"]
                picture = Picture()
                picture.mime, picture.type, picture.width, picture.height, picture.depth = "image/png", 3, 32, 32, 24
                picture.data = self.cover
                audio.add_picture(picture)
                audio.save()
        self.plan_path = Path(
            create_plan(self.root, self.storage / "relatorios/initial", storage=self.storage)["plano"]
        )
        self.app = App(self.root, self.storage)
        self.ids = [next(row["id"] for row in self.app.tracks if row["arquivo"] == str(path)) for path in self.paths]
        self.before = {path: sha256(path) for path in self.paths}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.stop)

    def stop(self):
        if self.app.worker:
            self.app.worker.join(10)
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.global_search.close()

    def request(self, route, data=None, status=200, **headers):
        defaults = {"Content-Type": "application/json", "Origin": self.url, "X-Library-Token": self.app.token}
        defaults.update(headers)
        request = Request(self.url + route, None if data is None else json.dumps(data).encode("utf-8"), defaults)
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            result = json.load(response)
            self.assertEqual(response.status, status, result)
            return result

    def editor(self, ident=None):
        return self.request("/api/albums/editor?id=" + (ident or self.ids[0]))

    def preview(self, fields, editor=None, status=200):
        editor = editor or self.editor()
        return self.request(
            "/api/albums/edit/preview",
            {"id": editor["id"], "album_revision": editor["album_revision"], "fields": fields},
            status=status,
        )

    def save(self, preview, status="completed"):
        self.request("/api/tracks/save", {"preview": preview["preview"]}, status=202)
        self.app.worker.join(10)
        self.assertEqual(self.app.job["status"], status, self.app.job)
        return to_api(self.app.job.get("result", {}))

    def assert_untouched(self):
        self.assertEqual({path: sha256(path) for path in self.paths}, self.before)
        self.assertFalse((self.storage / "backups").exists())

    def test_editor_reads_full_album_and_distinguishes_mixed_values_without_writes(self):
        editor = self.editor()
        self.assertEqual(editor["track_count"], 2)
        self.assertEqual([row["id"] for row in editor["tracks"]], self.ids[:2])
        self.assertEqual(editor["artist"], ["Various Artists"])
        self.assertEqual(editor["fields"]["album"], ["Same album title"])
        self.assertEqual(editor["fields"]["genre"], [])
        self.assertTrue(editor["field_status"]["genre"]["mixed"])
        self.assertTrue(editor["field_status"]["artist"]["mixed"])
        self.assertEqual(editor["field_status"]["genre"]["protected_count"], 0)
        self.assertEqual(editor["album_revision"], self.editor(self.ids[1])["album_revision"])
        self.assert_untouched()

    def test_preview_approve_and_restore_mp3_and_flac_preserve_all_unselected_data(self):
        before = {path: integrity(path) for path in self.paths[:2]}
        original_fields = {path: metadata_values(path) for path in self.paths[:2]}
        preview = self.preview({"genre": ["Chosen genre", "Second genre"]})
        self.assertEqual(preview["kind"], "album-edit")
        self.assertEqual(preview["count"], 2)
        self.assertEqual([row["id"] for row in preview["track_changes"]], self.ids[:2])
        self.assertTrue(
            all([change["field"] for change in row["changes"]] == ["genre"] for row in preview["track_changes"])
        )
        self.assert_untouched()
        result = self.save(preview)
        for path in self.paths[:2]:
            after = integrity(path)
            self.assertEqual(after["audio_sha256"], before[path]["audio_sha256"])
            if path.suffix == ".flac":
                self.assertEqual(after["blocos"], before[path]["blocos"])
            else:
                self.assertEqual(after["versao"], before[path]["versao"])
            self.assertEqual(
                metadata_values(path), {**original_fields[path], "genero": ["Chosen genre", "Second genre"]}
            )
            genre_key = "TCON" if path.suffix == ".mp3" else "genre"
            self.assertEqual(
                {key: value for key, value in after["tags"].items() if key != genre_key},
                {key: value for key, value in before[path]["tags"].items() if key != genre_key},
            )
        for path in self.paths[2:]:
            self.assertEqual(sha256(path), self.before[path])
        restore(Path(result["manifest"]), self.storage)
        self.assertEqual({path: sha256(path) for path in self.paths}, self.before)
        self.request("/api/tracks/save", {"preview": preview["preview"]}, status=400)

    def test_only_explicitly_selected_fields_change_including_explicit_clear(self):
        preview = self.preview({"album": ["New album name"], "year": ["2020"], "genre": []})
        self.save(preview)
        for path in self.paths[:2]:
            fields = metadata_values(path)
            self.assertEqual(fields["album"], ["New album name"])
            self.assertEqual(fields["ano"], ["2020"])
            self.assertEqual(fields["genero"], [])
            self.assertEqual(fields["artista_album"], ["Various Artists"])
            self.assertTrue(fields["faixa"])
            self.assertTrue(fields["titulo"])
        self.assertEqual(sha256(self.paths[2]), self.before[self.paths[2]])

    def test_protected_values_are_skipped_and_reported_per_track(self):
        protections.protect(self.storage, self.root, [str(self.paths[0])], ["genero"])
        editor = self.editor()
        self.assertEqual(editor["field_status"]["genre"]["protected_count"], 1)
        preview = self.preview({"genre": ["Chosen"]}, editor)
        self.assertEqual(preview["count"], 1)
        self.assertEqual(preview["protected_count"], 1)
        self.assertEqual(preview["skipped"], 1)
        self.assertEqual(preview["track_changes"][0]["protected_fields"], ["genre"])
        self.assertEqual(preview["track_changes"][0]["changes"], [])
        self.save(preview)
        self.assertEqual(sha256(self.paths[0]), self.before[self.paths[0]])
        self.assertEqual(metadata_values(self.paths[1])["genero"], ["Chosen"])

    def test_all_protected_or_no_changes_do_not_create_a_preview_or_backup(self):
        self.preview({"album": ["Same album title"]}, status=400)
        protections.protect(self.storage, self.root, [str(path) for path in self.paths[:2]], ["genero"])
        self.preview({"genre": ["Chosen"]}, status=400)
        self.assertFalse(self.app.manual_previews)
        self.assert_untouched()

    def test_invalid_fields_requests_and_unauthorized_writes_are_rejected(self):
        editor = self.editor()
        for fields in (
            {},
            {"title": ["Same title"]},
            {"track": ["1"]},
            {"genre": "Rock"},
            {"genre": [""]},
            {"genre": [1]},
            {"genre": ["x\x00"]},
        ):
            self.preview(fields, editor, status=400)
        self.request(
            "/api/albums/edit/preview",
            {"id": self.ids[0], "album_revision": editor["album_revision"], "fields": {"genre": ["Chosen"]}},
            status=403,
            **{"X-Library-Token": "wrong"},
        )
        self.request("/api/albums/editor?id=unknown", status=400)
        self.editor_error = self.request("/api/albums/editor?id=" + self.ids[3], status=400)
        self.assertIn("individually", self.editor_error["error"])
        self.assert_untouched()

    def test_changed_file_or_membership_since_editor_requires_a_new_review(self):
        editor = self.editor()
        audio = FLAC(self.paths[1])
        audio["genre"] = ["External edit"]
        audio.save()
        self.preview({"genre": ["Chosen"]}, editor, status=400)
        fresh = self.editor()
        self.app.tracks = [row for row in self.app.tracks if row["id"] != self.ids[1]]
        self.preview({"genre": ["Chosen"]}, fresh, status=400)
        self.assertFalse((self.storage / "backups").exists())
        self.assertEqual(sha256(self.paths[0]), self.before[self.paths[0]])

    def test_live_album_identity_differing_from_inventory_is_rejected(self):
        audio = FLAC(self.paths[1])
        audio["album"] = ["Another album"]
        audio.save()
        result = self.request("/api/albums/editor?id=" + self.ids[0], status=400)
        self.assertIn("refresh the library", result["error"])
        self.assertFalse((self.storage / "backups").exists())

    def test_file_or_protection_changes_after_preview_abort_before_any_writes(self):
        for change in ("file", "protection"):
            with self.subTest(change=change):
                preview = self.preview({"genre": ["Chosen"]})
                if change == "file":
                    with self.paths[1].open("ab") as stream:
                        stream.write(b"external edit")
                else:
                    protections.protect(self.storage, self.root, [str(self.paths[1])], ["genero"])
                self.save(preview, status="error")
                self.assertEqual(sha256(self.paths[0]), self.before[self.paths[0]])
                self.assertFalse((self.storage / "backups").exists())

    def test_symbolic_or_missing_member_cannot_expand_album_scope(self):
        original = self.paths[1].read_bytes()
        self.paths[1].unlink()
        self.request("/api/albums/editor?id=" + self.ids[0], status=400)
        external = self.root.parent / "external.flac"
        external.write_bytes(original)
        self.paths[1].symlink_to(external)
        self.request("/api/albums/editor?id=" + self.ids[0], status=400)
        self.assertEqual(external.read_bytes(), original)
        self.assertEqual(sha256(self.paths[0]), self.before[self.paths[0]])
        self.assertFalse((self.storage / "backups").exists())


if __name__ == "__main__":
    unittest.main()
