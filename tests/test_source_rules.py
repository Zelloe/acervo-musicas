"""Rules a linter cannot check, because it does not know a variable holds a Path."""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOT_FILES = {"os", "tarfile", "zipfile", "gzip"}


def text_call_without_encoding(call):
    function = call.func
    keywords = {item.arg: item.value for item in call.keywords if item.arg}
    if "encoding" in keywords:
        return False
    name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", None)
    if name in ("read_text", "write_text"):
        return True
    if name == "open":
        if isinstance(function, ast.Attribute) and getattr(function.value, "id", None) in NOT_FILES:
            return False
        positional = call.args[1:] if isinstance(function, ast.Name) else call.args
        mode = keywords.get("mode") or (positional[0] if positional else None)
        if mode is None:
            return True
        return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" not in mode.value
    if name in ("run", "Popen", "check_output"):
        text = keywords.get("text") or keywords.get("universal_newlines")
        return isinstance(text, ast.Constant) and text.value is True
    return False


class SourceRulesTest(unittest.TestCase):
    def test_text_files_and_processes_always_name_their_encoding(self):
        found = []
        for path in sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_bytes())):
                if isinstance(node, ast.Call) and text_call_without_encoding(node):
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(found, [], "The system default encoding differs between machines")

    def test_rule_recognizes_the_cases_it_exists_for(self):
        def flagged(code):
            return text_call_without_encoding(ast.parse(code, mode="eval").body)

        for code in (
            "path.read_text()",
            "path.read_text(errors='replace')",
            "path.write_text(x)",
            "path.open()",
            "path.open('a+')",
            "open(name)",
            "open(name, 'w')",
            "subprocess.run(c, text=True)",
        ):
            self.assertTrue(flagged(code), code)
        for code in (
            "path.read_text(encoding='utf-8')",
            "path.open('rb')",
            "open(name, 'rb')",
            "picker.open(data)",
            "os.open(name, flags)",
            "subprocess.run(c)",
            "path.read_bytes()",
        ):
            self.assertFalse(flagged(code), code)

    def test_project_and_setup_script_install_the_same_dependencies(self):
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = re.search(r"^dependencies = \[(.*?)\]", project, re.M | re.S).group(1)
        listed = (ROOT / "requirements.txt").read_text(encoding="utf-8").split()
        self.assertEqual(re.findall(r'"([^"]+)"', declared), listed)


if __name__ == "__main__":
    unittest.main()
