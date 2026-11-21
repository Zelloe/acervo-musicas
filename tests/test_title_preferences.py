"""Source initials do not replace known full titles; reviewed writes remain reversible."""

import copy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from acervo.downloads.staged import changes_for, is_title_abbreviation
from acervo.library.core import integrity, metadata_values, sha256, write_validated
from acervo.library.importer import import_reviewed, restore_import


class TitlePreferencesTest(unittest.TestCase):
    def test_only_matching_initials_are_classified_as_abbreviations(self):
        for full, short in (
            ("Curse of the Legions of Death", "C.O.T.L.O.D."),
            ("Master of Puppets", "mop"),
            ("Enter Sandman", "E.S."),
            ("Don't Fear the Reaper", "DFTR"),
        ):
            with self.subTest(full=full, short=short):
                self.assertTrue(is_title_abbreviation(full, short))
        for full, proposed in (
            ("Curse of the Legions of Death", "Curse Of The Legions Of Death"),
            ("Master of Puppets", "MoP (Live)"),
            ("Curse of the Legions of Death", "Other Title"),
            ("C.O.T.L.O.D.", "Curse of the Legions of Death"),
            ("Song", "S"),
        ):
            with self.subTest(full=full, proposed=proposed):
                self.assertFalse(is_title_abbreviation(full, proposed))

    def test_source_initials_keep_the_full_title_and_other_proposals(self):
        current = {"titulo": ["Curse of the Legions of Death"], "album": ["Old album"]}
        desired = {"titulo": ["C.O.T.L.O.D."], "album": ["The Legacy"]}
        original = copy.deepcopy((current, desired))
        self.assertEqual(
            changes_for(current, desired, True, True), {"album": {"atual": ["Old album"], "proposto": ["The Legacy"]}}
        )
        self.assertEqual((current, desired), original)
        self.assertEqual(changes_for({"titulo": []}, desired, True, True)["titulo"]["proposto"], ["C.O.T.L.O.D."])
        self.assertEqual(
            changes_for(current, {"titulo": ["Another full title"]}, True, True)["titulo"]["proposto"],
            ["Another full title"],
        )

    @unittest.skipUnless(shutil.which("ffmpeg"), "Temporary audio requires ffmpeg")
    def test_source_changes_preserve_full_title_audio_and_exact_restore(self):
        with tempfile.TemporaryDirectory(prefix="acervo-title-") as folder:
            base = Path(folder)
            root, storage = base / "music", base / "data"
            root.mkdir()
            for ext in ("mp3", "flac"):
                with self.subTest(format=ext):
                    path = root / ("Testament." + ext)
                    subprocess.run(
                        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=0.1", str(path)],
                        check=True,
                    )
                    write_validated(path, {"titulo": {"atual": [], "proposto": ["Curse of the Legions of Death"]}})
                    original, before = sha256(path), integrity(path)
                    changes = changes_for(
                        metadata_values(path),
                        {"titulo": ["C.O.T.L.O.D."], "album": ["The Legacy"], "genero": ["Thrash Metal"]},
                        True,
                        True,
                    )
                    result = import_reviewed(
                        root,
                        storage / "backups",
                        [{"acao": "metadados", "arquivo": str(path), "antes_sha256": original, "alteracoes": changes}],
                        "source-title-test",
                        storage,
                    )
                    self.assertEqual(metadata_values(path)["titulo"], ["Curse of the Legions of Death"])
                    self.assertEqual(metadata_values(path)["album"], ["The Legacy"])
                    self.assertEqual(integrity(path)["audio_sha256"], before["audio_sha256"])
                    restore_import(Path(result["manifesto"]))
                    self.assertEqual(sha256(path), original)
