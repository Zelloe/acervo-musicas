"""An explicit selection can repair tagless tracks without expanding its write scope."""

import unittest
from pathlib import Path

from mutagen.flac import FLAC

import test_album_edit_http as album_fixture
from acervo.library import protections
from acervo.library.core import integrity, metadata_values, restore, sha256


@unittest.skipUnless(album_fixture.shutil.which("ffmpeg"), "Temporary fixtures require ffmpeg")
class SelectionEditHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        album_fixture.AlbumEditHTTPTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        album_fixture.AlbumEditHTTPTest.tearDownClass()

    def setUp(self):
        self.fixture = album_fixture.AlbumEditHTTPTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app, self.root, self.storage = self.fixture.app, self.fixture.root, self.fixture.storage
        self.paths, self.ids, self.before = self.fixture.paths, self.fixture.ids, self.fixture.before
        self.request = self.fixture.request

    def editor(self, ids=None):
        return self.request("/api/tracks/batch/editor", {"ids": ids if ids is not None else [self.ids[3], self.ids[1]]})

    def preview(self, fields, editor=None, status=200, **overrides):
        editor = editor or self.editor()
        data = {"ids": editor["ids"], "batch_revision": editor["batch_revision"], "fields": fields, **overrides}
        return self.request("/api/tracks/batch/preview", data, status=status)

    def test_editor_includes_exact_selection_across_albums_and_tagless_files_without_writes(self):
        editor = self.editor()
        self.assertEqual(editor["kind"], "batch-edit")
        self.assertEqual(editor["track_count"], 2)
        self.assertEqual([row["id"] for row in editor["tracks"]], [self.ids[3], self.ids[1]])
        self.assertEqual(editor["tracks"][0]["fields"]["album"], [])
        self.assertTrue(editor["field_status"]["album"]["mixed"])
        self.fixture.assert_untouched()

    def test_explicit_selected_common_tags_write_and_restore_exactly_for_tagless_mp3_and_flac(self):
        paths = [self.paths[3], self.paths[1]]
        before = {path: integrity(path) for path in paths}
        fields_before = {path: metadata_values(path) for path in paths}
        fields = {"genre": ["Chosen genre"], "artist": ["Chosen artist"], "album": ["Chosen album"]}
        preview = self.preview(fields)
        self.assertEqual(preview["kind"], "batch-edit")
        self.assertEqual([row["id"] for row in preview["track_changes"]], [self.ids[3], self.ids[1]])
        self.fixture.assert_untouched()
        result = self.fixture.save(preview)
        for path in paths:
            values = metadata_values(path)
            self.assertEqual(
                values,
                {
                    **fields_before[path],
                    "genero": ["Chosen genre"],
                    "artista": ["Chosen artist"],
                    "album": ["Chosen album"],
                },
            )
            self.assertEqual(integrity(path)["audio_sha256"], before[path]["audio_sha256"])
            if path.suffix == ".flac":
                self.assertEqual(integrity(path)["blocos"], before[path]["blocos"])
                self.assertEqual(FLAC(path)["lyrics"], ["Keep FLAC lyrics"])
                self.assertEqual(FLAC(path)["musicbrainz_albumid"], ["keep-flac-id"])
        for path in [self.paths[0], self.paths[2]]:
            self.assertEqual(sha256(path), self.before[path])
        restore(Path(result["manifest"]), self.storage)
        self.assertEqual({path: sha256(path) for path in self.paths}, self.before)
        self.request("/api/tracks/save", {"preview": preview["preview"]}, status=400)

    def test_revision_cannot_be_reused_for_another_selection(self):
        editor = self.editor()
        self.preview({"genre": ["Chosen"]}, editor, status=400, ids=[self.ids[0], self.ids[1]])
        self.preview({"genre": ["Chosen"]}, editor, status=400, ids=[self.ids[1], self.ids[3]])
        self.fixture.assert_untouched()

    def test_invalid_or_duplicate_ids_and_authentication_never_create_writes(self):
        for ids in ([], [self.ids[0]] * 2, ["unknown"], [self.ids[0]] * 501, None, "all", [{}], [False]):
            self.request("/api/tracks/batch/editor", {"ids": ids}, status=400)
        self.request("/api/tracks/batch/editor", {"ids": [self.ids[0]], "files": [str(self.paths[0])]}, status=400)
        self.request("/api/tracks/batch/editor", {"ids": [self.ids[0]]}, status=403, **{"X-Library-Token": "wrong"})
        editor = self.editor()
        self.request(
            "/api/tracks/batch/preview",
            {"ids": editor["ids"], "batch_revision": editor["batch_revision"], "fields": {"genre": ["Chosen"]}},
            status=403,
            **{"Origin": "http://example.invalid"},
        )
        self.fixture.assert_untouched()

    def test_only_shared_fields_can_be_selected_and_explicit_empty_clears(self):
        editor = self.editor([self.ids[0], self.ids[1]])
        for fields in (
            {},
            {"title": ["Shared title"]},
            {"track": ["1"]},
            {"genre": "Rock"},
            {"genre": [""]},
            {"genre": [None]},
        ):
            self.preview(fields, editor, status=400)
        preview = self.preview({"genre": []}, editor)
        self.assertEqual(preview["count"], 1)
        self.fixture.save(preview)
        self.assertEqual(metadata_values(self.paths[0])["genero"], [])
        self.assertEqual(sha256(self.paths[1]), self.before[self.paths[1]])
        self.assertTrue(metadata_values(self.paths[0])["titulo"])
        self.assertEqual(metadata_values(self.paths[0])["faixa"], ["1"])

    def test_protections_apply_per_track_and_per_field(self):
        protections.protect(self.storage, self.root, [str(self.paths[0])], ["genero"])
        editor = self.editor(self.ids[:2])
        preview = self.preview({"genre": ["Chosen"], "year": ["2026"]}, editor)
        self.assertEqual(preview["count"], 2)
        self.assertEqual(preview["protected_count"], 1)
        self.assertEqual(preview["track_changes"][0]["protected_fields"], ["genre"])
        self.fixture.save(preview)
        self.assertEqual(metadata_values(self.paths[0])["genero"], ["Existing genre"])
        self.assertEqual(metadata_values(self.paths[1])["genero"], ["Chosen"])
        self.assertTrue(all(metadata_values(path)["ano"] == ["2026"] for path in self.paths[:2]))

    def test_all_protected_selection_has_no_preview_or_backup(self):
        protections.protect(self.storage, self.root, [str(path) for path in self.paths], ["genero"])
        self.preview({"genre": ["Chosen"]}, status=400)
        self.assertFalse(self.app.manual_previews)
        self.fixture.assert_untouched()

    def test_external_change_since_editor_requires_a_new_review(self):
        editor = self.editor()
        audio = FLAC(self.paths[1])
        audio["genre"] = ["External value"]
        audio.save()
        self.preview({"genre": ["Chosen"]}, editor, status=400)
        self.assertFalse((self.storage / "backups").exists())
        self.assertEqual(sha256(self.paths[3]), self.before[self.paths[3]])

    def test_file_or_policy_changes_after_preview_abort_the_whole_write(self):
        for change in ("file", "policy"):
            preview = self.preview({"genre": ["Chosen"]})
            if change == "file":
                with self.paths[1].open("ab") as stream:
                    stream.write(b"external edit")
            else:
                protections.protect(self.storage, self.root, [str(self.paths[1])], ["genero"])
            self.fixture.save(preview, status="error")
            self.assertEqual(sha256(self.paths[3]), self.before[self.paths[3]])
            self.assertFalse((self.storage / "backups").exists())

    def test_missing_or_symbolic_files_and_closed_library_refuse_selection(self):
        self.paths[3].unlink()
        self.request("/api/tracks/batch/editor", {"ids": [self.ids[3]]}, status=400)
        self.paths[3].symlink_to(self.paths[0])
        self.request("/api/tracks/batch/editor", {"ids": [self.ids[3]]}, status=400)
        self.app.closed = True
        self.request("/api/tracks/batch/editor", {"ids": self.ids[:2]}, status=400)
        self.assertEqual(sha256(self.paths[0]), self.before[self.paths[0]])
        self.assertFalse((self.storage / "backups").exists())


if __name__ == "__main__":
    unittest.main()
