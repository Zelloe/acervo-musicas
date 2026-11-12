"""Validate user-selected cover images without fetching external content."""

import base64
import binascii
import hashlib
import shutil
import struct
import subprocess
from functools import lru_cache

from mutagen.flac import Picture
from mutagen.id3 import APIC

from acervo.messages import _, register

register(
    {
        "Choose a JPEG or PNG image up to 5 MB": "Escolha uma imagem JPEG ou PNG de até 5 MB",
        "The cover image is invalid or too large": "A imagem de capa é inválida ou grande demais",
        "ffmpeg is required to validate the cover image": "O ffmpeg é necessário para validar a imagem de capa",
        "This file already has artwork; its existing images were preserved": "Este arquivo já tem capa; as imagens existentes foram preservadas",
    }
)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


def dimensions(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 33 and data[12:16] == b"IHDR":
        return "image/png", *struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 255:
                break
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in (0xD9, 0xDA):
                break
            size = int.from_bytes(data[offset : offset + 2], "big")
            if size < 2 or offset + size > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2) and size >= 8:
                height, width = struct.unpack(">HH", data[offset + 3 : offset + 7])
                return "image/jpeg", width, height
            offset += size
    raise ValueError(_("Choose a JPEG or PNG image up to 5 MB"))


def decode_cover(encoded):
    if not isinstance(encoded, str) or not encoded or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError(_("Choose a JPEG or PNG image up to 5 MB"))
    return _decode_cover(encoded)


@lru_cache(maxsize=2)
def _decode_cover(encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError(_("Choose a JPEG or PNG image up to 5 MB")) from error
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError(_("Choose a JPEG or PNG image up to 5 MB"))
    mime, width, height = dimensions(data)
    if not width or not height or width * height > MAX_IMAGE_PIXELS:
        raise ValueError(_("The cover image is invalid or too large"))
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError(_("ffmpeg is required to validate the cover image"))
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-xerror",
                "-nostdin",
                "-threads",
                "1",
                "-f",
                "image2pipe",
                "-c:v",
                "png" if mime == "image/png" else "mjpeg",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            input=data,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(_("The cover image is invalid or too large")) from error
    if result.returncode:
        raise ValueError(_("The cover image is invalid or too large"))
    return data, mime, width, height


def cover_summary(encoded):
    data, mime, width, height = decode_cover(encoded)
    return {
        "mime": mime,
        "width": width,
        "height": height,
        "tamanho_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def add_cover(tags, encoded, mp3):
    data, mime, width, height = decode_cover(encoded)
    if mp3:
        if tags.getall("APIC"):
            raise ValueError(_("This file already has artwork; its existing images were preserved"))
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    else:
        if tags.pictures or tags.get("metadata_block_picture"):
            raise ValueError(_("This file already has artwork; its existing images were preserved"))
        picture = Picture()
        picture.type, picture.mime, picture.desc = 3, mime, "Cover"
        picture.width, picture.height, picture.depth, picture.data = width, height, 24, data
        tags.add_picture(picture)
