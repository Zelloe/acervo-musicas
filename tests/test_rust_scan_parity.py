"""The Rust reader must give the same inventory row as the Python one, field for field."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, TXXX, TYER, UFID, USLT

from acervo.library.scan import read_track, scan
from acervo.messages import language

BINARY = Path(
    os.environ.get("ACERVO_SCAN_BIN", Path(__file__).resolve().parents[1] / "crates/acervo-core/target/release/scan")
)


@unittest.skipUnless(BINARY.is_file() and shutil.which("ffmpeg"), "Build crates/acervo-core with cargo to compare")
class RustScanParityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def audio(self, name, codec):
        path = self.root / name
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=1.3",
                "-c:a",
                codec,
                str(path),
            ],
            check=True,
        )
        return path

    def fixtures(self):
        cover = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
        v24 = self.audio("v24.mp3", "libmp3lame")
        tags = ID3()
        for frame in (
            TIT2(encoding=3, text=["Title", "Second value"]),
            TPE1(encoding=1, text=["AC/DC"]),
            TALB(encoding=0, text=["Álbum (Live)"]),
            TCON(encoding=3, text=["(17)", "Thrash Metal", "137"]),
            TDRC(encoding=3, text=["1980-07-25"]),
            TRCK(encoding=3, text=["3/12"]),
            TXXX(encoding=3, desc="MusicBrainz Album Id", text=["abc-123"]),
            UFID(owner="http://musicbrainz.org", data=b"recording-id"),
            APIC(encoding=3, mime="image/png", type=3, desc="", data=cover),
            APIC(encoding=3, mime="image/png", type=0, desc="Cover", data=cover[::-1]),
            COMM(encoding=3, lang="eng", desc="", text=["comment"]),
            USLT(encoding=3, lang="XXX", desc="", text="first line"),
        ):
            tags.add(frame)
        tags.save(v24, v2_version=4)
        v23 = self.audio("v23.mp3", "libmp3lame")
        tags = ID3()
        for frame in (
            TIT2(encoding=1, text=["Demo version"]),
            TPE2(encoding=0, text=["Banda/Outra"]),
            TYER(encoding=0, text=["1975"]),
            TCON(encoding=0, text=["(RX)(17)Rock"]),
        ):
            tags.add(frame)
        tags.save(v23, v2_version=3)
        untagged = self.audio("untagged.mp3", "libmp3lame")
        flac = self.audio("song.flac", "flac")
        audio = FLAC(flac)
        audio["title"], audio["ARTIST"], audio["date"] = "Música", ["One", "Two"], "2001"
        audio["MUSICBRAINZ_TRACKID"] = "track-id"
        picture = Picture()
        picture.type, picture.mime, picture.desc, picture.data = 3, "image/png", "front", cover
        audio.add_picture(picture)
        audio.save()
        return [v24, v23, untagged, flac]

    def test_both_readers_agree(self):
        files = self.fixtures()
        output = subprocess.run(
            [str(BINARY), *map(str, files)], capture_output=True, text=True, encoding="utf-8", check=True
        )
        rust = [json.loads(line) for line in output.stdout.splitlines()]
        for path, row in zip(files, rust, strict=True):
            with self.subTest(file=path.name):
                expected = read_track(path)
                self.assertAlmostEqual(row.pop("duracao_segundos"), expected.pop("duracao_segundos"), delta=1e-5)
                self.assertEqual(row, expected)

    def test_both_inventories_agree(self):
        library = self.root / "library"
        (library / "A b").mkdir(parents=True)
        (library / "A").mkdir()
        files = self.fixtures()
        files[0].rename(library / "A b" / "one.mp3")
        files[1].rename(library / "A" / "two.mp3")
        files[3].rename(library / "A" / "three.flac")
        for name, album in (
            ("deluxe.mp3", "Straße (Deluxe Edition)"),
            ("plain.mp3", "STRASSE"),
            ("live.mp3", "Strasse (Live)"),
        ):
            path = self.audio(name, "libmp3lame")
            tags = ID3()
            tags.add(TPE1(encoding=3, text=["Banda"]))
            tags.add(TALB(encoding=3, text=[album]))
            tags.save(path)
            path.rename(library / "A b" / name)
        (library / "A b" / "junk.mp3").write_bytes(b"not audio" * 100)
        (library / "A b" / "empty.flac").write_bytes(b"")
        (library / "A b" / "notes.txt").write_text("ignored", encoding="utf-8")
        (library / ".acervo-import-x.mp3").write_bytes(b"temp")
        (library / "link.mp3").symlink_to(library / "A" / "two.mp3")
        (library / "linked folder").symlink_to(library / "A")
        for code, lang in (("pt-BR", "pt_BR.UTF-8"), ("en", "C")):
            with self.subTest(language=code):
                python, rust = self.root / f"py-{code}", self.root / f"rs-{code}"
                with language(code):
                    scan(library, python)
                env = {**os.environ, "LANG": lang, "LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": ""}
                done = subprocess.run(
                    [str(BINARY.with_name("inventario")), "--pasta", str(library), "--saida", str(rust)],
                    capture_output=True,
                    env=env,
                )
                self.assertEqual(done.returncode, 2, done.stderr)
                for name in ("inventario.csv", "variantes_albuns.csv", "erros.csv"):
                    self.assertEqual(
                        (rust / name).read_text(encoding="utf-8"), (python / name).read_text(encoding="utf-8"), name
                    )
                for name in ("inventario.json", "resumo.json"):
                    expected, found = (
                        (folder / name).read_text(encoding="utf-8").splitlines() for folder in (python, rust)
                    )
                    stamp = [i for i, line in enumerate(expected) if '"gerado_em_utc"' in line]
                    for i in stamp:
                        self.assertRegex(
                            found[i], r'"gerado_em_utc": "\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?\+00:00"'
                        )
                        expected[i] = found[i]
                    self.assertEqual(found, expected, name)
                summary = json.loads((rust / "resumo.json").read_text(encoding="utf-8"))
                self.assertEqual(len(summary["erros"]), 2)
                self.assertEqual(len(summary["ignorados"]), 3)
                self.assertEqual(summary["grupos_candidatos_de_album"], 1)
