"""Select spotDL catalog results by server-owned IDs for the local interface."""

from __future__ import annotations

from messages import _, register

from collections import OrderedDict
from pathlib import Path
import secrets
import threading

import downloader


register({
    "Looking up edition tracks…": "Consultando as faixas da edição…",
    "Enter an existing folder path to save files": "Informe o caminho de uma pasta existente para salvar",
    "Use the full path of the destination folder": "Use o caminho completo da pasta para salvar",
    "The destination must be an existing folder": "O destino deve ser uma pasta existente",
    "Invalid result; search again": "Resultado inválido; faça a busca novamente",
    "The search returned invalid results": "A busca retornou resultados inválidos",
    "Checking tracks already in the folder…": "Conferindo as músicas que já estão na pasta…",
    "Choose a search mode and enter an artist, song or link": "Escolha como buscar e informe um artista, música ou link",
    "Finding artist albums…": "Buscando álbuns do artista…",
    "Finding tracks…": "Buscando músicas…",
    "Looking up tracks from the link…": "Consultando as faixas do link…",
    "Choose an album or edition from the search": "Escolha um álbum ou uma edição da busca",
    "The result does not belong to this search": "O resultado não pertence a esta busca",
    "Finding album editions…": "Buscando as edições do álbum…",
    "Select tracks from the search to download": "Selecione faixas da busca para baixar",
    "The selection does not belong to this search; search again": "A seleção não pertence à busca; procure novamente",
    "Invalid MP3 bitrate": "Taxa MP3 inválida",
    "Choose only track IDs returned by the search": "Escolha somente os identificadores de faixas retornadas pela busca",
    "The destination folder does not exist or cannot be accessed": "A pasta para salvar não existe ou não pode ser acessada",
    "The search expired; search again": "A busca expirou; procure novamente"
})


class DownloadPicker:
    def __init__(self, root: Path, progress=None, inventory=None, on_download=None):
        self.root = root
        self.folder = root
        self.progress = progress or (lambda *args: None)
        self.inventory = inventory
        self.on_download = on_download
        self.lock = threading.RLock()
        self.catalogs = OrderedDict()
        self.current = None
        self.results = []

    def local_index(self, folder):
        if self.inventory is None or not folder.is_relative_to(self.root):
            return downloader.local_index(folder)
        keys = set()
        for track in self.inventory():
            path = Path(track["arquivo"])
            if not path.is_relative_to(folder) or not path.is_file() or path.is_symlink():
                continue
            for artist in track["artista"] + track["artista_album"]:
                for title in track["titulo"]:
                    keys.add(downloader._key(artist, title))
        return keys

    def destination(self, value):
        if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(c) < 32 for c in value):
            raise ValueError(_('Enter an existing folder path to save files'))
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(_('Use the full path of the destination folder'))
        try:
            path = path.resolve(strict=True)
        except OSError as error:
            raise ValueError(_('The destination folder does not exist or cannot be accessed')) from error
        if not path.is_dir():
            raise ValueError(_('The destination must be an existing folder'))
        return path

    def _catalog(self, ident):
        if not isinstance(ident, str):
            raise ValueError(_('Invalid result; search again'))
        with self.lock:
            catalog = self.catalogs.get(ident)
            if catalog is None:
                raise ValueError(_('The search expired; search again'))
            return catalog

    def _remember(self, kind, title, rows, folder, artist=""):
        if not isinstance(rows, list) or len(rows) > 300 or any(not isinstance(row, dict) or not isinstance(row.get("titulo"), str) for row in rows):
            raise ValueError(_('The search returned invalid results'))
        rows = [{**row, "id": secrets.token_hex(12)} for row in rows]
        if kind == "faixas":
            self.progress(_('Checking tracks already in the folder…'), None)
            rows = downloader.mark_owned(rows, self.local_index(folder))
        ident = secrets.token_hex(16)
        catalog = {"id": ident, "tipo": kind, "titulo": title, "artista": artist, "pasta": str(folder), "itens": rows}
        with self.lock:
            self.catalogs[ident] = catalog
            self.current, self.folder = ident, folder
            while len(self.catalogs) > 40:
                self.catalogs.popitem(last=False)
        return {"catalogo": ident}

    def info(self, ident=None):
        with self.lock:
            catalog = self._catalog(ident) if ident else self.catalogs.get(self.current)
            return {"catalogo": catalog, "pasta": str(self.folder), "resultado": self.results}

    def search_options(self, data):
        kind, text = data.get("tipo"), data.get("consulta")
        if kind not in ("artista", "musica", "link") or not isinstance(text, str) or not text.strip() or len(text) > 1200 or any(ord(c) < 32 for c in text):
            raise ValueError(_('Choose a search mode and enter an artist, song or link'))
        folder = self.destination(data.get("pasta", str(self.folder)))
        return kind, text.strip(), folder

    def search(self, data):
        kind, text, folder = self.search_options(data)
        if kind == "artista":
            self.progress(_('Finding artist albums…'), None)
            return self._remember("albuns", text, downloader.search_albums(text), folder, text)
        self.progress(_('Finding tracks…') if kind == "musica" else _('Looking up tracks from the link…'), None)
        rows = downloader.search_songs(text) if kind == "musica" else downloader.tracks(text)
        return self._remember("faixas", text, rows, folder)

    def open_options(self, data):
        catalog = self._catalog(data.get("catalogo"))
        ident = data.get("item")
        if not isinstance(ident, str) or catalog["tipo"] not in ("albuns", "edicoes"):
            raise ValueError(_('Choose an album or edition from the search'))
        item = next((row for row in catalog["itens"] if row["id"] == ident), None)
        if item is None:
            raise ValueError(_('The result does not belong to this search'))
        return catalog, item, self.destination(data.get("pasta", catalog["pasta"]))

    def open(self, data):
        catalog, item, folder = self.open_options(data)
        if catalog["tipo"] == "albuns":
            self.progress(_('Finding album editions…'), None)
            rows = downloader.editions(catalog["artista"], item["titulo"])
            if len(rows) == 1:
                self.progress(_('Looking up edition tracks…'), None)
                return self._remember("faixas", rows[0]["titulo"], downloader.tracks(rows[0]["url"]), folder, catalog["artista"])
            return self._remember("edicoes", item["titulo"], rows, folder, catalog["artista"])
        self.progress(_('Looking up edition tracks…'), None)
        return self._remember("faixas", item["titulo"], downloader.tracks(item["url"]), folder, catalog["artista"])

    def download_options(self, data):
        catalog = self._catalog(data.get("catalogo"))
        ids = data.get("ids")
        bitrate = data.get("bitrate", "320k")
        if catalog["tipo"] != "faixas" or not isinstance(ids, list) or not ids or len(ids) > 300 or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
            raise ValueError(_('Select tracks from the search to download'))
        known = {row["id"]: row for row in catalog["itens"]}
        if any(ident not in known for ident in ids):
            raise ValueError(_('The selection does not belong to this search; search again'))
        if bitrate not in ("128k", "192k", "256k", "320k"):
            raise ValueError(_('Invalid MP3 bitrate'))
        if any(key in data for key in ("faixas", "url", "arquivo", "comando")):
            raise ValueError(_('Choose only track IDs returned by the search'))
        folder = self.destination(data.get("pasta", catalog["pasta"]))
        return catalog, [dict(known[ident]) for ident in ids], folder, bitrate

    def download(self, data):
        catalog, rows, folder, bitrate = self.download_options(data)
        results = downloader.download(rows, folder, progress=self.progress, bitrate=bitrate)
        with self.lock:
            self.results, self.folder = results, folder
        notices = self.on_download(results) if self.on_download else []
        result = self._remember("faixas", catalog["titulo"], catalog["itens"], folder, catalog["artista"])
        return {**result, "resultado": results, "avisos": notices}
