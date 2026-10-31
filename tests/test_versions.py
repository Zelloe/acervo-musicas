import unittest
from unittest.mock import patch

import downloader
from versions import classify


class VersionTest(unittest.TestCase):
    def labels(self, title, album="", kind=""):
        return classify(title, album, kind)["marcas"]

    def test_studio_titles_that_contain_live(self):
        self.assertEqual(self.labels("Live and Learn", "Hunters and Prey"), ["estudio"])
        self.assertEqual(self.labels("To Live Is to Die", "...And Justice for All"), ["estudio"])
        self.assertEqual(self.labels("Rock N Roll Train", "Long Live Rock n Roll"), ["estudio"])

    def test_live_from_title_or_album(self):
        self.assertEqual(self.labels("Heroes Of Sand - Ao Vivo", "Rebirth World Tour: Live in São Paulo (Ao Vivo)"), ["ao_vivo"])
        self.assertEqual(self.labels("Carry On", "Holy Live"), ["ao_vivo"])
        self.assertEqual(self.labels("Make Believe - Live at Porão do Rock"), ["ao_vivo"])
        self.assertEqual(self.labels("Carry On", "Angels Cry - 20th Anniversary Tour (Live)"), ["ao_vivo", "edicao_especial"])
        self.assertEqual(self.labels("Bleeding Heart - Live", "Acoustic - Live At Ópera De Arame"), ["ao_vivo", "acustico"])

    def test_other_versions(self):
        self.assertEqual(self.labels("Rebirth (Acoustic Version)", "Hunters and Prey"), ["acustico"])
        self.assertEqual(self.labels("Feel Your Weight - Angara Remix"), ["remix"])
        self.assertEqual(self.labels("Battery (Demo)"), ["demo"])
        self.assertEqual(self.labels("Orion [Instrumental]"), ["instrumental"])
        self.assertEqual(self.labels("Time - 2011 Remaster"), ["estudio", "remaster"])
        self.assertEqual(self.labels("Lisbon", "Anything", "compilation"), ["estudio", "coletanea"])
        self.assertEqual(self.labels("Carry On", "The Best Of Angra"), ["estudio", "coletanea"])
        self.assertEqual(classify("Carry On", "Holy Live")["rotulos"], ["Ao vivo"])

    def test_search_results_get_version_and_only_safe_photos(self):
        rows = [{"titulo": "Heroes Of Sand - Ao Vivo", "album": "Live in São Paulo", "tipo_album": "album", "capa": "https://i.scdn.co/image/abc"},
                {"titulo": "Nova Era", "album": "Rebirth", "tipo_album": "album", "capa": "https://evil.example/x.jpg"}]
        with patch.object(downloader, "_adapter", return_value=rows):
            result = downloader.search_songs("angra")
        self.assertEqual([r["versao"]["tipo"] for r in result], ["ao_vivo", "estudio"])
        self.assertEqual([r["capa"] for r in result], ["https://i.scdn.co/image/abc", ""])
        for url in ("https://lh3.googleusercontent.com/a=w120", "https://yt3.googleusercontent.com/x"):
            self.assertEqual(downloader.safe_photo(url), url)
        for bad in ("http://i.scdn.co/x", "https://i.scdn.co.evil.com/x", "javascript:alert(1)", None, 'https://i.scdn.co/x"onerror="1'):
            self.assertEqual(downloader.safe_photo(bad), "")


if __name__ == "__main__":
    unittest.main()
