//! User-facing messages: English source text with a Brazilian Portuguese catalogue,
//! like `acervo.messages` on the Python side.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Language {
    En,
    PtBr,
}

/// "pt", "pt_BR.UTF-8", "pt-br" -> Portuguese; "en_US" -> English; unknown -> None.
pub fn normalize_language(value: &str) -> Option<Language> {
    let code = value.trim().split('.').next()?.split('@').next()?;
    let code = code.replace('_', "-").to_lowercase();
    if code.starts_with("pt") {
        Some(Language::PtBr)
    } else if code.starts_with("en") {
        Some(Language::En)
    } else {
        None
    }
}

/// For command-line entry points: follow LANGUAGE/LC_ALL/LC_MESSAGES/LANG.
pub fn environment_language() -> Language {
    for name in ["LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"] {
        let value = std::env::var(name).unwrap_or_default();
        if let Some(found) = value.split(':').find_map(normalize_language) {
            return found;
        }
    }
    Language::En
}

const PT_BR: &[(&str, &str)] = &[
    ("File not recognized as MP3/FLAC", "Arquivo não reconhecido como MP3/FLAC"),
    ("The report output must be outside the library", "A saída dos relatórios deve ficar fora da biblioteca"),
    ("The output folder already exists; choose a new one to keep the existing reports", "A pasta de saída já existe; escolha uma nova para preservar os relatórios"),
    ("symbolic link to a folder", "diretório simbólico"),
    ("Acervo temporary copy", "cópia temporária do Acervo"),
    ("symbolic link to a file", "arquivo simbólico"),
    ("review required; recording not identified", "revisão obrigatória; sem identificação da gravação"),
    ("text similarity; does not confirm Release Group, Release or Recording", "semelhança textual; não confirma Release Group, Release ou Recording"),
    ("The inventory is not a restorable backup of the metadata.", "O inventário não é um backup restaurável dos metadados."),
    ("Stability compares size, mtime, ctime, device and inode; it does not hash the full audio.", "A estabilidade compara tamanho, mtime, ctime, dispositivo e inode; não calcula hash integral do áudio."),
    ("Reading may update atime, depending on the file system configuration.", "A leitura pode atualizar atime conforme a configuração do sistema de arquivos."),
    ("Version markers and album groups are heuristics for human review.", "Marcadores de versão e grupos de álbum são heurísticas para revisão humana."),
    ("It does not identify recordings, detect duplicates or approve changes.", "Não identifica gravações, não detecta duplicatas e não aprova alterações."),
    ("This program made no external queries.", "Nenhuma consulta externa foi feita por este programa."),
    ("Music folder does not exist: {root}", "Pasta de músicas inexistente: {root}"),
    ("Error: {error}", "Erro: {error}"),
    ("Reports: {output}", "Relatórios: {output}"),
    ("Protected field: {field}", "Campo protegido: {field}"),
    ("Album rule", "Regra por álbum"),
    ("Rule for the exact artist credit; requires approval", "Regra por crédito exato de artista; requer aprovação"),
    ("No genre identified", "Sem gênero identificado"),
    ("Genre has no rule or conflicting rules", "Gênero sem regra ou regras conflitantes"),
    ("Candidate album with mismatched version/track/disc; keep and review", "Álbum candidato com versão/faixa/disco incompatível; preservar e revisar"),
    ("Album already has a previous name recorded; review needed", "Álbum já tem registro de nome anterior; revisão necessária"),
    ("Artist, album, disc, track and title match; recording not confirmed", "Correspondência de artista, álbum, disco, faixa e título; gravação não confirmada"),
    ("Current name kept in BibliotecaMusical:AlbumAnterior (MP3) or BIBLIOTECA_ALBUM_ANTERIOR (FLAC)", "Nome atual preservado em BibliotecaMusical:AlbumAnterior (MP3) ou BIBLIOTECA_ALBUM_ANTERIOR (FLAC)"),
    ("The library must be a folder and the reports must be kept outside it", "A biblioteca deve ser uma pasta e os relatórios devem ficar fora dela"),
    ("The output folder already exists; choose another one", "A pasta de saída já existe; escolha outra"),
    ("File changed during the analysis", "Arquivo mudou durante a análise"),
    ("File with hard links; approval blocked", "Arquivo com hard links; aprovação bloqueada"),
    ("Analysis complete", "Análise concluída"),
    ("Invalid ID3 header", "Cabeçalho ID3 inválido"),
    ("Writing is limited to ID3v2.3/v2.4; file sent for review", "Gravação restrita a ID3v2.3/v2.4; arquivo encaminhado para revisão"),
    ("Unsupported format", "Formato não suportado"),
    ("Invalid FLAC header", "Cabeçalho FLAC inválido"),
    ("Truncated FLAC block", "Bloco FLAC truncado"),
    ("Could not read the protected fields; nothing will be written: {path}", "Não foi possível ler os campos protegidos; nenhuma gravação será feita: {path}"),
    ("The protected fields file must be a regular file, not a link: {path}", "O arquivo de campos protegidos deve ser um arquivo comum, não um link: {path}"),
    ("Unreadable protected fields file; nothing will be written: {path}", "Arquivo de campos protegidos ilegível; nenhuma gravação será feita: {path}"),
    ("Incompatible protected fields file; nothing will be written: {path}", "Arquivo de campos protegidos incompatível; nenhuma gravação será feita: {path}"),
    ("The protected fields file belongs to another library: {path}", "O arquivo de campos protegidos pertence a outra biblioteca: {path}"),
];

/// The message in the chosen language; English text without an entry is returned as is.
pub fn tr(language: Language, text: &'static str) -> &'static str {
    if language == Language::PtBr {
        if let Some((_, translated)) = PT_BR.iter().find(|(source, _)| *source == text) {
            return translated;
        }
    }
    text
}
