import csv
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mutagen.id3 import ID3, TCON, TIT2, TPE1

import protections
from library_core import apply_plan, library_lock, metadata_values, restore, sha256
from staged_downloads import changes_for
from library_import import import_reviewed, restore_import
from library_plan import create_plan


@unittest.skipUnless(shutil.which("ffmpeg"), "Os testes de integração usam ffmpeg para gerar áudio temporário")
class ProtectionTest(unittest.TestCase):
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
        base = Path(self.temp.name)
        self.lib = base / "musicas"
        self.lib.mkdir()
        self.data = base / "dados"
        self.base = base
        self.track = self.mp3("acdc.mp3", "Back In Black")

    def tearDown(self):
        self.temp.cleanup()

    def mp3(self, name, title, genre=None):
        path = self.lib / name
        shutil.copy(self.sample, path)
        tags = ID3()
        tags.add(TIT2(encoding=3, text=[title]))
        tags.add(TPE1(encoding=3, text=["AC/DC"]))
        if genre:
            tags.add(TCON(encoding=3, text=[genre]))
        tags.save(path, v2_version=3)
        return path

    def plan(self, name="plano"):
        result = create_plan(self.lib, self.base / name, storage=self.data)
        plan = Path(result["plano"])
        approvals = plan.parent / "alteracoes.csv"
        with approvals.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        for row in rows:
            row["aprovar"] = "sim"
        with approvals.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return plan, approvals, rows

    def protect(self, *fields, path=None):
        return protections.protect(self.data, self.lib, [str(path or self.track)], list(fields))

    # Política

    def test_policy_roundtrip_and_validation(self):
        self.assertEqual(protections.load(self.data, self.lib)["faixas"], {})
        self.protect("genero", "album")
        policy = protections.load(self.data, self.lib)
        self.assertEqual(protections.protected(policy, self.lib, str(self.track)), {"genero", "album"})
        self.assertEqual(protections.listing(policy)[0]["campos"], ["album", "genero"])
        protections.unprotect(self.data, self.lib, [str(self.track)], ["album", "genero"])
        self.assertEqual(protections.load(self.data, self.lib)["faixas"], {})
        with self.assertRaises(ValueError):
            self.protect("capa")
        outside = self.base / "fora.mp3"
        shutil.copy(self.sample, outside)
        with self.assertRaises(ValueError):
            self.protect("genero", path=outside)

    def test_corrupted_or_foreign_policy_blocks_writes(self):
        self.protect("genero")
        path = protections.policy_path(self.data, self.lib)
        path.write_text("{quebrado")
        with self.assertRaisesRegex(ValueError, "ilegível"):
            protections.load(self.data, self.lib)
        with self.assertRaisesRegex(ValueError, "ilegível"):
            create_plan(self.lib, self.base / "plano", storage=self.data)
        path.write_text(json.dumps({"schema": 1, "biblioteca": "/outra/colecao", "faixas": {}}))
        with self.assertRaisesRegex(ValueError, "outra biblioteca"):
            protections.load(self.data, self.lib)

    def test_unreadable_or_odd_policy_files_fail_closed(self):
        self.protect("genero")
        path = protections.policy_path(self.data, self.lib)
        for bad in ({"schema": 1, "biblioteca": str(self.lib.resolve()), "faixas": {"acdc.mp3": {"campos": [{"x": 1}]}}},
                    {"schema": 1, "biblioteca": str(self.lib.resolve()), "faixas": {"acdc.mp3": ["genero"]}},
                    {"schema": 1, "biblioteca": str(self.lib.resolve()), "faixas": {"../fora.mp3": {"campos": ["genero"]}}}):
            path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, "incompatível"):
                protections.load(self.data, self.lib)
        path.unlink()
        path.symlink_to(self.base / "inexistente.json")
        with self.assertRaisesRegex(ValueError, "arquivo comum"):
            protections.load(self.data, self.lib)
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(ValueError, "arquivo comum"):
            protections.load(self.data, self.lib)
        path.rmdir()
        with patch.object(Path, "lstat", side_effect=PermissionError("negado")):
            with self.assertRaisesRegex(ValueError, "Não foi possível ler"):
                protections.load(self.data, self.lib)

    def test_each_library_has_its_own_policy(self):
        other = self.base / "outra"
        other.mkdir()
        shutil.copy(self.track, other / "acdc.mp3")
        self.protect("genero")
        self.assertEqual(protections.load(self.data, other)["faixas"], {})

    def test_busy_library_gives_clear_error(self):
        with library_lock(self.lib):
            with self.assertRaisesRegex(ValueError, "ocupada"):
                self.protect("genero")

    # Propostas

    def test_empty_protected_field_is_not_proposed(self):
        _, _, rows = self.plan()
        self.assertEqual([r["campo"] for r in rows], ["genero"])
        self.protect("genero")
        result = create_plan(self.lib, self.base / "plano2", storage=self.data)
        self.assertEqual(json.loads(Path(result["plano"]).read_text())["arquivos"], [])
        self.assertEqual(result["resumo"]["alteracoes_propostas"], 0)
        self.assertIn("Campo protegido: genero", (self.base / "plano2" / "revisao.csv").read_text(encoding="utf-8-sig"))

    def test_download_changes_skip_protected_fields(self):
        current = {"genero": [], "album": ["Velho"]}
        desired = {"genero": ["Hard Rock"], "album": ["Novo"]}
        self.assertEqual(set(changes_for(current, desired, True, True, 3, frozenset({"genero"}))), {"album"})

    # Gravação e restauração de metadados

    def test_plan_made_before_protection_is_refused(self):
        before = self.track.read_bytes()
        plan, approvals, _ = self.plan()
        self.protect("genero")
        with self.assertRaisesRegex(ValueError, "Campo protegido"):
            apply_plan(plan, approvals, self.data / "backups", self.data)
        self.assertEqual(self.track.read_bytes(), before)
        self.assertFalse((self.data / "backups").exists())

    def test_default_storage_matches_backup_parent(self):
        plan, approvals, _ = self.plan()
        self.protect("genero")
        with self.assertRaisesRegex(ValueError, "Campo protegido"):
            apply_plan(plan, approvals, self.data / "backups")

    def test_restore_refuses_to_change_protected_field(self):
        plan, approvals, _ = self.plan()
        result = apply_plan(plan, approvals, self.data / "backups", self.data)
        applied = self.track.read_bytes()
        self.protect("genero")
        with self.assertRaisesRegex(ValueError, "campo protegido"):
            restore(Path(result["manifesto"]))
        self.assertEqual(self.track.read_bytes(), applied)
        protections.unprotect(self.data, self.lib, [str(self.track)], ["genero"])
        restore(Path(result["manifesto"]))
        self.assertEqual(metadata_values(self.track)["genero"], [])

    def test_protecting_unrelated_field_does_not_block_restore(self):
        plan, approvals, _ = self.plan()
        result = apply_plan(plan, approvals, self.data / "backups", self.data)
        self.protect("titulo")
        restore(Path(result["manifesto"]))
        self.assertEqual(metadata_values(self.track)["genero"], [])

    # Importação

    def operation(self, action, changes, source=None):
        op = {"acao": action, "arquivo": str(self.track), "antes_sha256": sha256(self.track), "alteracoes": changes}
        if source:
            op.update(origem=str(source), origem_sha256=sha256(source))
        return op

    def test_import_refuses_protected_change(self):
        self.protect("genero")
        op = self.operation("metadados", {"genero": {"atual": [], "proposto": ["Hard Rock"]}})
        with self.assertRaisesRegex(ValueError, "Campo protegido"):
            import_reviewed(self.lib, self.data / "backups", [op], "lote", self.data)

    def test_replacement_keeps_protected_values(self):
        self.protect("titulo")
        staging = self.base / "staging"
        staging.mkdir()
        source = staging / "baixada.mp3"
        shutil.copy(self.sample, source)
        tags = ID3()
        tags.add(TIT2(encoding=3, text=["Back In Black (Remastered)"]))
        tags.save(source, v2_version=3)
        import_reviewed(self.lib, self.data / "backups", [self.operation("substituir", {}, source)], "lote", self.data)
        self.assertEqual(metadata_values(self.track)["titulo"], ["Back In Black"])

    def test_import_undo_refuses_to_change_protected_field(self):
        op = self.operation("metadados", {"genero": {"atual": [], "proposto": ["Hard Rock"]}})
        result = import_reviewed(self.lib, self.data / "backups", [op], "lote", self.data)
        self.protect("genero")
        with self.assertRaisesRegex(ValueError, "campo protegido"):
            restore_import(Path(result["manifesto"]))
        self.assertEqual(metadata_values(self.track)["genero"], ["Hard Rock"])


if __name__ == "__main__":
    unittest.main()
