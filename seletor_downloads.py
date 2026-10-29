"""Seleção por IDs de resultados do spotDL para a interface local."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import secrets
import threading

import baixador


class SeletorDownloads:
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
            return baixador.indice_local(folder)
        keys = set()
        for track in self.inventory():
            path = Path(track["arquivo"])
            if not path.is_relative_to(folder) or not path.is_file() or path.is_symlink():
                continue
            for artist in track["artista"] + track["artista_album"]:
                for title in track["titulo"]:
                    keys.add(baixador._key(artist, title))
        return keys

    def destination(self, value):
        if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(c) < 32 for c in value):
            raise ValueError("Informe o caminho de uma pasta existente para salvar")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("Use o caminho completo da pasta para salvar")
        try:
            path = path.resolve(strict=True)
        except OSError as error:
            raise ValueError("A pasta para salvar não existe ou não pode ser acessada") from error
        if not path.is_dir():
            raise ValueError("O destino deve ser uma pasta existente")
        return path

    def _catalog(self, ident):
        if not isinstance(ident, str):
            raise ValueError("Resultado inválido; faça a busca novamente")
        with self.lock:
            catalog = self.catalogs.get(ident)
            if catalog is None:
                raise ValueError("A busca expirou; procure novamente")
            return catalog

    def _remember(self, kind, title, rows, folder, artist=""):
        if not isinstance(rows, list) or len(rows) > 300 or any(not isinstance(row, dict) or not isinstance(row.get("titulo"), str) for row in rows):
            raise ValueError("A busca retornou resultados inválidos")
        rows = [{**row, "id": secrets.token_hex(12)} for row in rows]
        if kind == "faixas":
            self.progress("Conferindo as músicas que já estão na pasta…", None)
            rows = baixador.ja_tenho(rows, self.local_index(folder))
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
            raise ValueError("Escolha como buscar e informe um artista, música ou link")
        folder = self.destination(data.get("pasta", str(self.folder)))
        return kind, text.strip(), folder

    def search(self, data):
        kind, text, folder = self.search_options(data)
        if kind == "artista":
            self.progress("Buscando álbuns do artista…", None)
            return self._remember("albuns", text, baixador.buscar_albuns(text), folder, text)
        self.progress("Buscando músicas…" if kind == "musica" else "Consultando as faixas do link…", None)
        rows = baixador.buscar_musicas(text) if kind == "musica" else baixador.faixas(text)
        return self._remember("faixas", text, rows, folder)

    def open_options(self, data):
        catalog = self._catalog(data.get("catalogo"))
        ident = data.get("item")
        if not isinstance(ident, str) or catalog["tipo"] not in ("albuns", "edicoes"):
            raise ValueError("Escolha um álbum ou uma edição da busca")
        item = next((row for row in catalog["itens"] if row["id"] == ident), None)
        if item is None:
            raise ValueError("O resultado não pertence a esta busca")
        return catalog, item, self.destination(data.get("pasta", catalog["pasta"]))

    def open(self, data):
        catalog, item, folder = self.open_options(data)
        if catalog["tipo"] == "albuns":
            self.progress("Buscando as edições do álbum…", None)
            rows = baixador.edicoes(catalog["artista"], item["titulo"])
            if len(rows) == 1:
                self.progress("Consultando as faixas da edição…", None)
                return self._remember("faixas", rows[0]["titulo"], baixador.faixas(rows[0]["url"]), folder, catalog["artista"])
            return self._remember("edicoes", item["titulo"], rows, folder, catalog["artista"])
        self.progress("Consultando as faixas da edição…", None)
        return self._remember("faixas", item["titulo"], baixador.faixas(item["url"]), folder, catalog["artista"])

    def download_options(self, data):
        catalog = self._catalog(data.get("catalogo"))
        ids = data.get("ids")
        bitrate = data.get("bitrate", "320k")
        if catalog["tipo"] != "faixas" or not isinstance(ids, list) or not ids or len(ids) > 300 or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("Selecione faixas da busca para baixar")
        known = {row["id"]: row for row in catalog["itens"]}
        if any(ident not in known for ident in ids):
            raise ValueError("A seleção não pertence à busca; procure novamente")
        if bitrate not in ("128k", "192k", "256k", "320k"):
            raise ValueError("Taxa MP3 inválida")
        if any(key in data for key in ("faixas", "url", "arquivo", "comando")):
            raise ValueError("Escolha somente os identificadores de faixas retornadas pela busca")
        folder = self.destination(data.get("pasta", catalog["pasta"]))
        return catalog, [dict(known[ident]) for ident in ids], folder, bitrate

    def download(self, data):
        catalog, rows, folder, bitrate = self.download_options(data)
        results = baixador.baixar(rows, folder, progresso=self.progress, bitrate=bitrate)
        with self.lock:
            self.results, self.folder = results, folder
        notices = self.on_download(results) if self.on_download else []
        result = self._remember("faixas", catalog["titulo"], catalog["itens"], folder, catalog["artista"])
        return {**result, "resultado": results, "avisos": notices}
