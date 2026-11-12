"""Internet Archive search, item listing and verified FLAC downloads, without the network."""

import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from mutagen.flac import FLAC

from acervo.downloads import archive, downloader


class Response(io.BytesIO):
    def __init__(self, data, url, status=200, cut=None):
        super().__init__(data)
        self.url, self.status, self.cut = url, status, cut

    def geturl(self):
        return self.url

    def read(self, size=-1):
        # Simulates a server that drops the connection after `cut` bytes.
        if self.cut is not None and self.tell() >= self.cut:
            raise ConnectionResetError("connection dropped")
        if self.cut is not None and size > 0:
            size = min(size, self.cut - self.tell())
        return super().read(size)


def json_response(data, url="https://archive.org/metadata/x"):
    return Response(json.dumps(data).encode(), url)


class ArchiveListingTest(unittest.TestCase):
    def test_search_sends_plain_words_and_keeps_where_items_come_from(self):
        docs = [
            {
                "identifier": "gd1977-05-08",
                "title": "Grateful Dead Live at Barton Hall",
                "creator": ["Grateful Dead"],
                "date": "1977-05-08T00:00:00Z",
                "collection": ["fav-someone", "GratefulDead", "etree"],
                "licenseurl": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
            },
            {"identifier": "../etc", "title": "Bad id"},
            {"identifier": "no-title"},
        ]
        with patch.object(archive, "urlopen", return_value=json_response({"response": {"docs": docs}})) as opened:
            items = archive.search('Grateful "Dead" AND (1977) -cornell')
        query = parse_qs(urlsplit(opened.call_args[0][0].full_url).query)["q"][0]
        self.assertEqual(query, "(Grateful Dead AND 1977 cornell) AND mediatype:(audio) AND format:(flac)")
        self.assertEqual(len(items), 1)
        self.assertEqual(
            {k: items[0][k] for k in ("titulo", "artista", "ano", "colecao", "provedor", "ia_item")},
            {
                "titulo": "Grateful Dead Live at Barton Hall",
                "artista": "Grateful Dead",
                "ano": "1977",
                "colecao": "GratefulDead",
                "provedor": "internet_archive",
                "ia_item": "gd1977-05-08",
            },
        )

    def test_item_lists_original_flac_files_in_track_order(self):
        md5 = "0" * 32
        files = [
            {"name": "d1t02.flac", "format": "Flac", "source": "original", "size": "10", "md5": md5, "track": "2"},
            {"name": "d1t01 Bertha.flac", "format": "24bit Flac", "source": "original", "size": "10", "md5": md5},
            {"name": "derived.flac", "format": "Flac", "source": "derivative", "size": "10", "md5": md5},
            {"name": "../escape.flac", "format": "Flac", "source": "original", "size": "10", "md5": md5},
            {"name": "bad-md5.flac", "format": "Flac", "source": "original", "size": "10", "md5": "x"},
            {"name": "notes.txt", "format": "Text", "source": "original", "size": "10", "md5": md5},
        ]
        data = {"metadata": {"title": "Show", "creator": "Band", "year": "1977"}, "files": files}
        with patch.object(archive, "urlopen", return_value=json_response(data)):
            rows = archive.tracks("show1977")
        self.assertEqual([r["ia_arquivo"] for r in rows], ["d1t02.flac", "d1t01 Bertha.flac"])
        self.assertEqual([r["titulo"] for r in rows], ["d1t02", "Bertha"])
        self.assertEqual(rows[0]["url"], "https://archive.org/download/show1977/d1t02.flac")
        self.assertEqual((rows[1]["artista"], rows[1]["album"], rows[1]["tamanho_bytes"]), ("Band", "Show", 10))
        with self.assertRaises(ValueError):
            archive.tracks("../etc")

    def test_derived_flac_is_used_when_there_is_no_original(self):
        files = [{"name": "01. Loser.flac", "format": "Flac", "source": "derivative", "size": "5", "md5": "1" * 32}]
        with patch.object(archive, "urlopen", return_value=json_response({"metadata": {}, "files": files})):
            self.assertEqual([r["titulo"] for r in archive.tracks("item")], ["Loser"])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is needed to make and check FLAC files")
class ArchiveDownloadTest(unittest.TestCase):
    URL = "https://archive.org/download/show1977/01.flac"
    SERVER = "https://ia800106.us.archive.org/20/items/show1977/01.flac"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        source = self.folder / "source.flac.tmp"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-f", "flac", str(source)], check=True
        )
        self.data = source.read_bytes()
        source.unlink()
        self.item = {
            "titulo": "Bertha",
            "artista": "Grateful Dead",
            "album": "Barton Hall",
            "ano": "1977",
            "numero": 1,
            "url": self.URL,
            "provedor": "internet_archive",
            "tamanho_bytes": len(self.data),
            "md5": hashlib.md5(self.data).hexdigest(),
        }

    def responses(self, *answers):
        calls = []

        def opened(request, timeout):
            calls.append(request.get_header("Range"))
            answer = answers[len(calls) - 1]
            if isinstance(answer, Exception):
                raise answer
            return answer

        return calls, patch.object(archive, "urlopen", side_effect=opened)

    def download(self, *answers):
        calls, opened = self.responses(*answers)
        with opened, patch.object(downloader, "spotdl_executable", side_effect=AssertionError("spotDL not needed")):
            result = downloader.download([self.item], self.folder, lyrics=False)[0]
        return result, calls

    def test_download_resumes_checks_md5_and_fills_empty_tags(self):
        half = len(self.data) // 2
        result, calls = self.download(
            Response(self.data, self.SERVER, cut=half),
            TimeoutError("slow server"),
            Response(self.data[half:], self.SERVER, status=206),
        )
        self.assertEqual(result["estado"], "baixado", result)
        self.assertEqual(calls, [None, f"bytes={half}-", f"bytes={half}-"])
        final = Path(result["arquivo"])
        self.assertEqual(final.name, "Grateful Dead - Bertha.flac")
        tags = FLAC(final)
        self.assertEqual((tags["title"], tags["album"], tags["tracknumber"]), (["Bertha"], ["Barton Hall"], ["1"]))
        self.assertEqual([p.name for p in self.folder.iterdir()], [final.name])  # no temporary file left

    def test_a_server_that_ignores_the_range_starts_over(self):
        half = len(self.data) // 2
        result, calls = self.download(
            Response(self.data, self.SERVER, cut=half), Response(self.data, self.SERVER, status=200)
        )
        self.assertEqual(result["estado"], "baixado", result)
        self.assertEqual(calls, [None, f"bytes={half}-"])

    def test_wrong_content_and_foreign_hosts_publish_nothing(self):
        damaged = bytearray(self.data)
        damaged[-1] ^= 1
        for answer, message in (
            (Response(bytes(damaged), self.SERVER), "MD5"),
            (Response(self.data + b"x", self.SERVER), "larger"),
            (Response(self.data, "https://example.com/01.flac"), "archive.org"),
        ):
            with self.subTest(message=message):
                result, _calls = self.download(answer)
                self.assertEqual(result["estado"], "erro")
                self.assertIn(message, result["mensagem"])
                self.assertEqual(list(self.folder.iterdir()), [])

    def test_existing_file_is_never_replaced(self):
        existing = self.folder / "Grateful Dead - Bertha.flac"
        existing.write_bytes(b"mine")
        result, _calls = self.download(Response(self.data, self.SERVER))
        self.assertEqual(result["estado"], "ja_existia")
        self.assertEqual(existing.read_bytes(), b"mine")

    def test_rows_that_did_not_come_from_the_listing_are_refused(self):
        for change in ({"url": "https://example.com/x.flac"}, {"md5": "nope"}, {"tamanho_bytes": "10"}):
            with self.subTest(change=change):
                self.item.update(change)
                result, calls = self.download()
                self.assertEqual((result["estado"], calls), ("erro", []))
                self.setUp()


if __name__ == "__main__":
    unittest.main()
