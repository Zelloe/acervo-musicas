import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mutagen.id3 import ID3, TIT2, TLEN, TPE1

import library_import
from library_core import sha256
from library_import import import_reviewed, restore_import
from library_scan import scan


@unittest.skipUnless(shutil.which("ffmpeg"), "Os testes de integração usam ffmpeg para gerar áudio temporário")
class ImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.TemporaryDirectory()
        cls.sample_root = Path(cls.samples.name)
        for name, frequency, duration in (("original.mp3", 440, 0.1), ("download.mp3", 880, 0.2)):
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}", "-c:a", "libmp3lame", str(cls.sample_root / name)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.samples.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.lib = base / "musicas"
        self.lib.mkdir()
        self.backups = base / "backups"
        self.staging = base / "staging"
        self.staging.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def mp3(self, folder: Path, name: str, sample: str, title: str, length: str) -> Path:
        path = folder / name
        shutil.copy(self.sample_root / sample, path)
        tags = ID3()
        tags.add(TIT2(encoding=3, text=[title]))
        tags.add(TPE1(encoding=3, text=["Banda"]))
        tags.add(TLEN(encoding=3, text=[length]))
        tags.save(path, v2_version=3)
        return path

    def replace_op(self, target: Path, source: Path) -> dict:
        return {"acao": "substituir", "arquivo": str(target), "antes_sha256": sha256(target), "origem": str(source), "origem_sha256": sha256(source), "alteracoes": {}}

    def add_op(self, target: Path, source: Path) -> dict:
        return {"acao": "adicionar", "arquivo": str(target), "origem": str(source), "origem_sha256": sha256(source), "alteracoes": {}}

    def manifest(self, error: RuntimeError) -> Path:
        return Path(str(error).split("restaurar: ")[1].split(". ")[0])

    def test_add_and_undo_archives_copy(self):
        source = self.mp3(self.staging, "nova.mp3", "download.mp3", "Nova", "200")
        target = self.lib / "Banda - Nova.mp3"
        result = import_reviewed(self.lib, self.backups, [self.add_op(target, source)], "lote")
        self.assertEqual(sha256(target), sha256(source))
        undo = restore_import(Path(result["manifesto"]))
        self.assertFalse(target.exists())
        archived = Path(result["manifesto"]).parent / "retiradas" / "000000.mp3"
        self.assertEqual(sha256(archived), sha256(source))
        self.assertEqual(undo["avisos"], [])

    def test_replace_keeps_tags_uses_download_length_and_restores_exactly(self):
        target = self.mp3(self.lib, "faixa.mp3", "original.mp3", "Original", "100")
        original = target.read_bytes()
        source = self.mp3(self.staging, "baixada.mp3", "download.mp3", "Baixada", "200")
        result = import_reviewed(self.lib, self.backups, [self.replace_op(target, source)], "lote")
        tags = ID3(target)
        self.assertEqual(tags["TIT2"].text, ["Original"])
        self.assertEqual(tags["TLEN"].text, ["200"])
        restore_import(Path(result["manifesto"]))
        self.assertEqual(target.read_bytes(), original)

    def test_interrupted_batch_can_be_undone_without_touching_foreign_file(self):
        replaced = self.mp3(self.lib, "faixa.mp3", "original.mp3", "Original", "100")
        original = replaced.read_bytes()
        source = self.mp3(self.staging, "baixada.mp3", "download.mp3", "Baixada", "200")
        added = self.lib / "Banda - Nova.mp3"
        ops = [self.replace_op(replaced, source), self.add_op(added, source)]

        real_link = library_import.os.link

        def foreign_file_appears(src, dst):
            if Path(dst) != added:
                return real_link(src, dst)
            Path(dst).write_bytes(b"outro programa")
            raise FileExistsError(dst)

        with patch.object(library_import.os, "link", side_effect=foreign_file_appears):
            with self.assertRaises(RuntimeError) as caught:
                import_reviewed(self.lib, self.backups, ops, "lote")
        self.assertNotEqual(replaced.read_bytes(), original)
        result = restore_import(self.manifest(caught.exception))
        self.assertEqual(replaced.read_bytes(), original)
        self.assertEqual(added.read_bytes(), b"outro programa")
        self.assertEqual(len(result["avisos"]), 1)

    def test_entries_never_published_are_skipped_on_undo(self):
        first = self.mp3(self.lib, "a.mp3", "original.mp3", "A", "100")
        second = self.mp3(self.lib, "b.mp3", "original.mp3", "B", "100")
        source = self.mp3(self.staging, "baixada.mp3", "download.mp3", "Baixada", "200")
        first_bytes = first.read_bytes()
        ops = [self.replace_op(first, source), self.replace_op(second, source)]
        real_replace = library_import.os.replace
        calls = []

        def edited_by_other_program(src, dst):
            if Path(dst).parent == self.lib:  # atomic_json also uses os.replace
                calls.append(dst)
                if len(calls) == 2:
                    raise ValueError("simulação de falha")
            return real_replace(src, dst)

        with patch.object(library_import.os, "replace", side_effect=edited_by_other_program):
            with self.assertRaises(RuntimeError) as caught:
                import_reviewed(self.lib, self.backups, ops, "lote")
        second.write_bytes(second.read_bytes() + b"\0")  # external edit after the failure
        second_bytes = second.read_bytes()
        restore_import(self.manifest(caught.exception))
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertEqual(second.read_bytes(), second_bytes)

    def test_add_refused_up_front_without_hard_links(self):
        replaced = self.mp3(self.lib, "faixa.mp3", "original.mp3", "Original", "100")
        original = replaced.read_bytes()
        source = self.mp3(self.staging, "nova.mp3", "download.mp3", "Nova", "200")
        ops = [self.replace_op(replaced, source), self.add_op(self.lib / "Banda - Nova.mp3", source)]
        with patch.object(library_import.os, "link", side_effect=OSError("exFAT")):
            with self.assertRaisesRegex(ValueError, "hard links"):
                import_reviewed(self.lib, self.backups, ops, "lote")
        self.assertEqual(replaced.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in self.lib.iterdir()), ["faixa.mp3"])
        self.assertFalse(self.backups.exists())

    def test_leftover_temporary_copy_is_moved_out_and_ignored(self):
        source = self.mp3(self.staging, "nova.mp3", "download.mp3", "Nova", "200")
        target = self.lib / "Banda - Nova.mp3"
        real_link = library_import.os.link

        def fails_on_publish(src, dst):
            if Path(dst) == target:
                raise OSError("falha ao publicar")
            return real_link(src, dst)

        with patch.object(library_import.os, "link", side_effect=fails_on_publish):
            with self.assertRaises(RuntimeError) as caught:
                import_reviewed(self.lib, self.backups, [self.add_op(target, source)], "lote")
        manifest_path = self.manifest(caught.exception)
        entry = json.loads(manifest_path.read_text())["arquivos"][0]
        leftover = Path(entry["temporario"])
        self.assertTrue(leftover.name.startswith(".acervo-import-"))
        shutil.copy(source, leftover)  # simulates a process killed before the finally
        summary = scan(self.lib, Path(self.temp.name) / "inventario")
        self.assertIn(str(leftover), [item["arquivo"] for item in summary["ignorados"]])
        restore_import(manifest_path)
        self.assertFalse(leftover.exists())
        self.assertEqual(sha256(manifest_path.parent / "retiradas" / "temporario-000000.mp3"), sha256(source))


if __name__ == "__main__":
    unittest.main()
