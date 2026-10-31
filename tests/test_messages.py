import os
import threading
import unittest
from unittest.mock import patch

import messages
from messages import _, current_language, language, normalize_language


class MessagesTest(unittest.TestCase):
    def setUp(self):
        self.saved = dict(messages.PT_BR)
        messages.register({"The folder does not exist: {path}": "A pasta não existe: {path}", "Done": "Concluído"})

    def tearDown(self):
        messages.PT_BR.clear()
        messages.PT_BR.update(self.saved)

    def test_language_codes(self):
        for value, expected in (("pt", "pt-BR"), ("pt_BR.UTF-8", "pt-BR"), ("PT-br", "pt-BR"), ("en_US", "en"), ("en", "en"), ("de_DE", None), ("", None), (None, None)):
            self.assertEqual(normalize_language(value), expected)

    def test_library_default_is_english_and_cli_follows_environment(self):
        with patch.dict(os.environ, {"LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": "", "LANG": "pt_BR.UTF-8"}):
            self.assertEqual(current_language(), "en")  # same results on any machine
            self.assertEqual(messages.environment_language(), "pt-BR")
            try:
                self.assertEqual(messages.use_environment_language(), "pt-BR")
                self.assertEqual(_("Done"), "Concluído")
            finally:
                messages._default = "en"
        with patch.dict(os.environ, {"LANGUAGE": "de:en", "LC_ALL": "", "LANG": "pt_BR.UTF-8"}):
            self.assertEqual(messages.environment_language(), "en")
        with patch.dict(os.environ, {"LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": "", "LANG": "C"}):
            self.assertEqual(messages.environment_language(), "en")

    def test_request_language_and_placeholders(self):
        with patch.dict(os.environ, {"LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": "", "LANG": "en_US.UTF-8"}):
            self.assertEqual(_("The folder does not exist: {path}", path="/x"), "The folder does not exist: /x")
            with language("pt-BR"):
                self.assertEqual(_("The folder does not exist: {path}", path="/x"), "A pasta não existe: /x")
                self.assertEqual(_("Untranslated text"), "Untranslated text")
            with language("klingon"):
                self.assertEqual(_("Done"), "Done")
            self.assertEqual(_("Done"), "Done")

    def test_language_is_per_thread(self):
        seen = {}

        def worker(code):
            with language(code):
                threading.Event().wait(0.05)
                seen[code] = _("Done")

        threads = [threading.Thread(target=worker, args=(code,)) for code in ("pt-BR", "en")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(seen, {"pt-BR": "Concluído", "en": "Done"})

    def test_conflicting_translation_is_refused(self):
        with self.assertRaises(ValueError):
            messages.register({"Done": "Feito"})


if __name__ == "__main__":
    unittest.main()
