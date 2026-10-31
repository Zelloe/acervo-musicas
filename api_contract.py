"""Translate the HTTP contract without changing existing files on disk.

Storage dictionaries retain their original schema. Only structural keys and
known enum values are translated; music metadata and external provider data
must pass through unchanged.
"""

from messages import _, register

register({
    "Duplicate request fields": "Campos repetidos na requisição"
})


KEYS = {
    "acao": "action", "album_atual": "current_album", "album_base_candidato": "candidate_album_base",
    "albuns": "albums", "alteracoes": "changes", "alteracoes_propostas": "proposed_changes",
    "alteracoes_substituicao": "replacement_changes", "alvo": "target", "amostra": "sample",
    "analisado_em": "analyzed_at", "ano": "year", "ano_original": "original_year",
    "anterior_sha256": "previous_sha256", "antes_sha256": "before_sha256", "aprovar": "approve",
    "arquivo": "file", "arquivo_estavel_ate_fim_varredura": "file_stable_until_scan_end",
    "arquivo_estavel_durante_leitura": "file_stable_during_read", "arquivo_novo": "new_file",
    "arquivos": "files", "arquivos_alterados": "changed_files_count", "arquivos_restaurados": "restored_files_count", "arquivos_com_mudanca_detectada": "changed_files",
    "arquivos_com_propostas": "files_with_proposals", "artista": "artist", "artista_album": "album_artist",
    "artista_grupo": "group_artist", "artistas": "artists", "assinatura_antes": "before_signature",
    "atual": "current", "atualizado_em": "updated_at", "ausentes": "missing", "avisos": "warnings",
    "baixadas": "downloaded", "biblioteca": "library", "biblioteca_revisao": "library_revision",
    "bits_por_amostra": "bits_per_sample", "campo": "field", "campos": "fields",
    "campos_ausentes": "missing_fields", "campos_protegidos": "protected_fields",
    "canais": "channels", "cancelamento_solicitado": "cancellation_requested", "capas": "covers", "capa": "cover",
    "catalogo": "catalog", "categoria": "category", "chave_album_candidato": "candidate_album_key",
    "chaves_tags": "tag_keys", "com_capa": "with_cover",
    "com_identificadores_musicbrainz": "with_musicbrainz_ids",
    "com_marcadores_de_versao": "with_version_markers", "consulta": "query", "consultas_musicbrainz": "musicbrainz_requests", "contato": "contact",
    "correspondencias": "matches", "criado": "created", "criado_em": "created_at",
    "criterio": "criterion", "data": "date", "dados": "data", "descricao": "description",
    "destino": "destination", "diferenca_duracao": "duration_difference", "disco": "disc",
    "disponivel": "available", "dispositivo": "device", "duracao": "duration",
    "duracao_compativel": "duration_matches", "duracao_segundos": "duration_seconds",
    "encontrada": "found", "encontradas": "found_count", "encontrados": "found_files",
    "entradas": "entries", "erro": "error", "erro_protecoes": "protection_error", "erros": "errors",
    "escopo": "scope", "estado": "status", "evidencia": "evidence", "execucoes": "runs",
    "executavel": "executable", "existe": "exists", "faixa": "track", "faixas": "tracks", "faixas_total": "track_count",
    "ferramenta": "tool", "ferramentas": "tools", "fonte": "source", "forcar_genero": "force_genre",
    "formato": "format", "formato_tags": "tag_format", "formatos": "formats", "foto": "photo",
    "genero": "genre", "genero_preenchido": "filled_genre", "gerado_em_utc": "generated_at_utc",
    "grupos_candidatos_de_album": "candidate_album_groups", "ignorados": "skipped",
    "importacao": "import", "iniciado": "started", "itens": "items", "ja_tenho": "already_owned",
    "letra": "lyrics", "letra_track_id": "lyrics_track_id", "lidos": "read_count", "limites": "limits", "linha": "line",
    "manifesto": "manifest", "pasta_relatorios": "report_folder", "lote": "batch", "lotes": "batches", "marcadores_versao": "version_markers", "marcas": "marks",
    "mensagem": "message", "metadados": "metadata", "modo": "mode", "motivo": "reason",
    "musicas": "tracks", "nome": "name", "numero": "number", "opcoes": "options",
    "operacoes": "operations", "observacoes": "notes", "preservacao": "preservation", "origem_genero": "genre_source", "pasta": "folder", "pastas": "folders",
    "plano": "plan", "politica": "policy", "pontuacao": "score", "previa": "preview",
    "progresso": "progress", "propostas": "proposal_count", "propostas_por_campo": "proposals_by_field",
    "proposto": "proposed", "protecoes": "protections", "protecoes_revisao": "protection_revision",
    "proteger": "protect", "registro": "log", "reinicio_necessario": "restart_required",
    "resultado": "result", "revisoes": "review_count", "resumo": "summary", "rotulos": "labels", "selecoes": "selections",
    "sincronizada": "synced", "somente_leitura": "read_only", "sobrescrever": "overwrite",
    "tamanho_bytes": "size_bytes", "termos": "terms", "texto": "text", "tipo": "type",
    "titulo": "title", "total_albuns": "album_count", "total_artistas": "artist_count",
    "unificar_album": "unify_album", "versao": "version", "versao_id3": "id3_version",
}

ENUMS = {
    "acustico": "acoustic", "adicionar": "add", "analise": "analysis", "ao_vivo": "live",
    "aplicacao": "apply", "aplicando": "applying", "artista": "artist", "albuns": "albums",
    "baixado": "downloaded", "baixando": "downloading", "biblioteca": "library",
    "busca_direta": "direct_search", "cancelado": "cancelled", "catalogado": "cataloged",
    "catalogo": "catalog", "coletanea": "compilation", "completo": "full", "concluido": "completed",
    "consultando": "querying", "download_direto": "direct_download", "edicao_especial": "special_edition",
    "edicoes": "editions", "erro": "error", "estudio": "studio", "executando": "running",
    "faixas": "tracks", "faltantes": "missing", "importacao": "import", "importado": "imported",
    "interrompido": "interrupted", "ja_existia": "already_existed", "letra": "lyrics",
    "metadados": "metadata", "musica": "song", "nao_baixado": "not_downloaded",
    "preparando_backups": "preparing_backups", "pronto": "ready", "restauracao": "restore",
    "restaurado": "restored", "revisao": "review", "sem-genero": "missing-genre",
    "substituir": "replace", "todas": "all", "versoes": "versions",
}

FIELDS = {key: KEYS.get(key, key) for key in ("titulo", "artista", "artista_album", "album", "genero", "ano", "faixa", "disco", "letra")}
ENUM_KEYS = {"tipo", "estado", "acao", "modo", "categoria", "escopo", "filter", "versao"}
FIELD_LISTS = {"campos", "campos_ausentes", "campos_protegidos"}

# Only request keys have an inverse: response keys need not be one-to-one.
REQUEST_KEYS = {KEYS[key]: key for key in (
    "acao", "alvo", "anterior_sha256", "arquivo", "arquivos", "artista", "artista_album", "ano",
    "campos", "catalogo", "consulta", "contato", "destino", "disco", "escopo", "faixa", "faixas",
    "ferramenta", "forcar_genero", "genero", "modo", "nome", "pasta", "previa", "proteger",
    "selecoes", "sobrescrever", "texto", "tipo", "titulo", "unificar_album", "versao",
)}
REVERSE_ENUMS = {value: key for key, value in ENUMS.items()}
REVERSE_FIELDS = {value: key for key, value in FIELDS.items()}


def _convert(value, incoming=False, context=""):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            mapped = REQUEST_KEYS.get(key, key) if incoming else KEYS.get(key, key)
            if mapped in result:
                raise ValueError(_('Duplicate request fields'))
            legacy_key = mapped if incoming else key
            # Provider records and MusicBrainz identifiers have their own schemas.
            if legacy_key == "musicbrainz" or legacy_key == "fonte" and isinstance(item, dict):
                result[mapped] = item
            elif not incoming and legacy_key == "fonte" and "encontrada" in value and item == "arquivo":
                result[mapped] = "file"
            else:
                result[mapped] = _convert(item, incoming, legacy_key)
        return result
    if isinstance(value, (list, tuple)):
        return [_convert(item, incoming, context) for item in value]
    if isinstance(value, str):
        if context == "campo" or context in FIELD_LISTS:
            return (REVERSE_FIELDS if incoming else FIELDS).get(value, value)
        if context == "marcas" or context in ENUM_KEYS:
            return (REVERSE_ENUMS if incoming else ENUMS).get(value, value)
    return value


def to_api(value):
    """Return the English HTTP representation without mutating storage data."""
    return _convert(value)


def from_api(value):
    """Decode English request fields for the unchanged storage/core schemas."""
    return _convert(value, incoming=True)
