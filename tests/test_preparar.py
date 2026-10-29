"""Falhas de inicialização não podem apagar o ambiente anterior do usuário."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("bash"), "O inicializador usa bash")
class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="acervo-bootstrap-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "Projeto com espaço"
        self.project.mkdir()
        self.fakebin = self.base / "bin"
        self.fakebin.mkdir()
        fake = self.fakebin / "python3"
        fake.write_text('''#!/bin/sh
if [ "$1" = "-c" ]; then exit 0; fi
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  mkdir -p "$3"
  printf parcial > "$3/parcial.txt"
  exit 1
fi
exit 1
''')
        fake.chmod(0o755)
        self.script = Path(__file__).resolve().parents[1] / "preparar.sh"

    def prepare(self):
        return subprocess.run([shutil.which("bash"), "-c", 'source "$1"; acervo_preparar', "bash", str(self.script)],
                              cwd=self.project, env={"PATH": str(self.fakebin) + os.pathsep + os.defpath, "HOME": str(self.base)},
                              capture_output=True, text=True, timeout=5)

    def test_failed_venv_creation_preserves_existing_environment(self):
        previous = self.project / ".venv"
        previous.mkdir()
        sentinel = previous / "preservar.txt"
        sentinel.write_text("Dados anteriores")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(sentinel.is_file(), "A falha apagou o ambiente anterior")
        self.assertEqual(sentinel.read_text(), "Dados anteriores")
        self.assertIn("python3-venv", result.stderr)

    def test_failed_new_environment_is_cleaned_up(self):
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.project / ".venv").exists())
        self.assertIn("python3-venv", result.stderr)

    def test_old_python_in_existing_venv_is_not_accepted(self):
        executable = self.project / ".venv/bin/python"
        executable.parent.mkdir(parents=True)
        executable.write_text('''#!/bin/sh
case "$*" in
  *sys.version_info*) exit 1 ;;
  *mutagen*) exit 0 ;;
esac
exit 0
''')
        executable.chmod(0o755)
        sentinel = executable.parent.parent / "preservar.txt"
        sentinel.write_text("Ambiente com Python antigo")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0, "O fast-path aceitou Python antigo só por conter mutagen")
        self.assertTrue(sentinel.is_file())

    def test_successful_venv_command_cannot_leave_an_old_interpreter_in_use(self):
        executable = self.project / ".venv/bin/python"
        executable.parent.mkdir(parents=True)
        executable.write_text('''#!/bin/sh
case "$*" in
  *sys.version_info*) exit 1 ;;
esac
exit 0
''')
        executable.chmod(0o755)
        (self.fakebin / "python3").write_text("#!/bin/sh\nexit 0\n")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("3.10", result.stderr)
        self.assertTrue(executable.is_file())


if __name__ == "__main__":
    unittest.main()
