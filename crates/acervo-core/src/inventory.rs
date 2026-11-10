//! Read-only diagnosis of the whole library: the same reports as `acervo.library.scan.scan`.

use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use regex::Regex;
use serde_json::{json, Map, Value};
use unicode_normalization::UnicodeNormalization;

use crate::messages::{tr, Language};
use crate::scan::{read_track, signature, FIELDS, REQUIRED};

/// Temporary copies Acervo creates next to the original while writing.
pub const TEMP_PREFIXES: [&str; 5] = [
    ".acervo-import-",
    ".acervo-undo-",
    ".acervo-baixando-",
    ".biblioteca-",
    ".restaurar-",
];

/// Python's `str.split()` whitespace, which also includes the ASCII separators 0x1C-0x1F.
fn python_space(c: char) -> bool {
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}

pub fn normalized(value: &str) -> String {
    let folded = caseless::default_case_fold_str(&value.nfkc().collect::<String>());
    folded
        .split(python_space)
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Heuristic used only in the report, never as identification.
pub fn album_base(value: &str) -> String {
    static PATTERNS: OnceLock<(Regex, Regex)> = OnceLock::new();
    let (edition, suffix) = PATTERNS.get_or_init(|| {
        (
            Regex::new(r"(?i)\b(remaster(?:ed)?|deluxe|expanded|anniversary|box set)\b")
                .expect("valid pattern"),
            Regex::new(r"\s*[\[(]([^\[\]()]+)[\])]\s*$").expect("valid pattern"),
        )
    });
    let mut value = value
        .nfkc()
        .collect::<String>()
        .trim_matches(python_space)
        .to_string();
    while let Some(found) = suffix.captures(&value) {
        let label = &found[1];
        if !edition.is_match(label) || crate::scan::version_pattern().is_match(label) {
            break;
        }
        let start = found.get(0).unwrap().start();
        value = value[..start].trim_matches(python_space).to_string();
    }
    value
}

/// `Path.suffix` in Python: empty for dotfiles such as ".mp3".
fn suffix(name: &str) -> String {
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => name[i..].to_lowercase(),
        _ => String::new(),
    }
}

/// `str(OSError)` as Python prints it for a folder `os.walk` could not open.
fn walk_error(path: &Path, error: &io::Error) -> String {
    let text = error.to_string();
    let message = text.split(" (os error").next().unwrap_or(&text);
    match error.raw_os_error() {
        Some(code) => format!("[Errno {code}] {message}: '{}'", path.display()),
        None => message.to_string(),
    }
}

struct Walk {
    files: Vec<PathBuf>,
    skipped: Vec<Value>,
    errors: Vec<Value>,
}

/// Top-down walk in directory order, like `os.walk(root, followlinks=False)`.
fn walk(folder: &Path, language: Language, found: &mut Walk) {
    let entries = match fs::read_dir(folder) {
        Ok(entries) => entries,
        Err(error) => {
            found.errors.push(
                json!({"arquivo": folder.to_string_lossy(), "erro": walk_error(folder, &error)}),
            );
            return;
        }
    };
    let (mut dirs, mut names) = (Vec::new(), Vec::new());
    for entry in entries.flatten() {
        // os.walk sorts entries by whether they point at a folder, following links.
        if fs::metadata(entry.path())
            .map(|m| m.is_dir())
            .unwrap_or(false)
        {
            dirs.push(entry.path());
        } else {
            names.push(entry.path());
        }
    }
    dirs.retain(|directory| {
        let link = directory.symlink_metadata().map(|m| m.file_type().is_symlink()).unwrap_or(false);
        if link {
            found.skipped.push(json!({"arquivo": directory.to_string_lossy(), "motivo": tr(language, "symbolic link to a folder")}));
        }
        !link
    });
    for path in names {
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        if suffix(&name) != ".mp3" && suffix(&name) != ".flac" {
            continue;
        }
        if TEMP_PREFIXES.iter().any(|prefix| name.starts_with(prefix)) {
            found.skipped.push(json!({"arquivo": path.to_string_lossy(), "motivo": tr(language, "Acervo temporary copy")}));
        } else if path
            .symlink_metadata()
            .map(|m| m.file_type().is_symlink())
            .unwrap_or(false)
        {
            found.skipped.push(json!({"arquivo": path.to_string_lossy(), "motivo": tr(language, "symbolic link to a file")}));
        } else {
            found.files.push(path);
        }
    }
    for directory in dirs {
        walk(&directory, language, found);
    }
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// `datetime.now(timezone.utc).isoformat()`.
fn now_iso() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let (days, seconds) = ((now.as_secs() / 86400) as i64, now.as_secs() % 86400);
    // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
    let z = days + 719468;
    let era = z.div_euclid(146097);
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    let micros = now.subsec_micros();
    let time = format!(
        "{:02}:{:02}:{:02}",
        seconds / 3600,
        seconds / 60 % 60,
        seconds % 60
    );
    if micros == 0 {
        format!("{year:04}-{month:02}-{day:02}T{time}+00:00")
    } else {
        format!("{year:04}-{month:02}-{day:02}T{time}.{micros:06}+00:00")
    }
}

/// `repr(float)`: shortest round trip, always with a dot or an exponent.
fn python_float(value: f64) -> String {
    if value.is_finite() && value != 0.0 && (value.abs() >= 1e16 || value.abs() < 1e-4) {
        let text = format!("{value:e}");
        let (mantissa, exponent) = text.split_once('e').unwrap();
        let exponent: i32 = exponent.parse().unwrap();
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{mantissa}e{sign}{:02}", exponent.abs())
    } else {
        format!("{value:?}")
    }
}

/// `json.dumps(value, ensure_ascii=False)`, with Python's ", " and ": " separators.
fn python_json(value: &Value) -> String {
    match value {
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(python_json).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, v)| format!("{}: {}", Value::from(k.as_str()), python_json(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Number(n) if n.is_f64() => python_float(n.as_f64().unwrap()),
        other => other.to_string(),
    }
}

/// One CSV cell as `csv.DictWriter` writes it.
fn cell(value: Option<&Value>) -> String {
    let text = match value {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Bool(b)) => if *b { "True" } else { "False" }.to_string(),
        Some(Value::Number(n)) if n.is_f64() => python_float(n.as_f64().unwrap()),
        Some(Value::Number(n)) => n.to_string(),
        Some(other) => python_json(other),
    };
    if text.contains([',', '"', '\r', '\n']) {
        format!("\"{}\"", text.replace('"', "\"\""))
    } else {
        text
    }
}

pub fn write_csv(path: &Path, rows: &[Value], fields: &[&str]) -> io::Result<()> {
    let mut out = String::from("\u{feff}");
    out.push_str(&fields.join(","));
    out.push_str("\r\n");
    for row in rows {
        let cells: Vec<String> = fields.iter().map(|field| cell(row.get(*field))).collect();
        out.push_str(&cells.join(","));
        out.push_str("\r\n");
    }
    fs::write(path, out)
}

/// `json.dumps(value, ensure_ascii=False, indent=2)`.
fn python_pretty(value: &Value, indent: usize, out: &mut String) {
    let pad = "  ".repeat(indent + 1);
    match value {
        Value::Array(items) if !items.is_empty() => {
            out.push_str("[\n");
            for (i, item) in items.iter().enumerate() {
                out.push_str(&pad);
                python_pretty(item, indent + 1, out);
                out.push_str(if i + 1 < items.len() { ",\n" } else { "\n" });
            }
            out.push_str(&"  ".repeat(indent));
            out.push(']');
        }
        Value::Object(map) if !map.is_empty() => {
            out.push_str("{\n");
            for (i, (key, item)) in map.iter().enumerate() {
                out.push_str(&pad);
                out.push_str(&Value::from(key.as_str()).to_string());
                out.push_str(": ");
                python_pretty(item, indent + 1, out);
                out.push_str(if i + 1 < map.len() { ",\n" } else { "\n" });
            }
            out.push_str(&"  ".repeat(indent));
            out.push('}');
        }
        other => out.push_str(&python_json(other)),
    }
}

pub fn pretty(value: &Value) -> String {
    let mut out = String::new();
    python_pretty(value, 0, &mut out);
    out
}

#[derive(Debug)]
pub enum ScanError {
    Invalid(&'static str),
    Io(io::Error),
}

impl From<io::Error> for ScanError {
    fn from(error: io::Error) -> Self {
        ScanError::Io(error)
    }
}

/// Reads every file, several at a time, keeping the results in the order of `files`.
fn read_all(files: &[PathBuf]) -> Vec<Result<Value, String>> {
    let workers = std::thread::available_parallelism()
        .map_or(1, |n| n.get())
        .min(8);
    let chunk = files.len().div_ceil(workers).max(1);
    std::thread::scope(|scope| {
        let handles: Vec<_> = files
            .chunks(chunk)
            .map(|part| {
                scope.spawn(|| part.iter().map(|path| read_track(path)).collect::<Vec<_>>())
            })
            .collect();
        handles
            .into_iter()
            .flat_map(|handle| handle.join().expect("reader thread"))
            .collect()
    })
}

pub fn scan(root: &Path, output: &Path, language: Language) -> Result<Value, ScanError> {
    // Never writes reports into the library nor allows overwriting a previous run.
    if output.starts_with(root) {
        return Err(ScanError::Invalid(tr(
            language,
            "The report output must be outside the library",
        )));
    }
    if output.exists() || output.symlink_metadata().is_ok() {
        return Err(ScanError::Invalid(tr(
            language,
            "The output folder already exists; choose a new one to keep the existing reports",
        )));
    }

    let mut found = Walk {
        files: Vec::new(),
        skipped: Vec::new(),
        errors: Vec::new(),
    };
    walk(root, language, &mut found);
    let Walk {
        mut files,
        skipped,
        mut errors,
    } = found;
    files.sort();
    let mut tracks = Vec::new();
    for (path, result) in files.iter().zip(read_all(&files)) {
        match result {
            Ok(row) => tracks.push(row),
            Err(error) => {
                // Python names the exception before the message.
                let error = match error.as_str() {
                    "File not recognized as MP3/FLAC" => {
                        format!(
                            "ValueError: {}",
                            tr(language, "File not recognized as MP3/FLAC")
                        )
                    }
                    other => other.to_string(),
                };
                errors.push(json!({"arquivo": path.to_string_lossy(), "erro": error}));
            }
        }
    }

    let mut changed = Vec::new();
    for row in &mut tracks {
        let path = PathBuf::from(row["arquivo"].as_str().unwrap_or_default());
        let stable = row["arquivo_estavel_durante_leitura"] == json!(true)
            && signature(&path)
                .map(|now| now == row["assinatura_antes"])
                .unwrap_or(false);
        row["arquivo_estavel_ate_fim_varredura"] = json!(stable);
        if !stable {
            changed.push(row["arquivo"].clone());
        }
    }

    let mut groups: BTreeMap<(Vec<String>, String), Vec<&Value>> = BTreeMap::new();
    for row in &tracks {
        // Different credits stay separate; AC/DC is never split at the slash.
        let mut credit = strings(&row["artista_album"]);
        if credit.is_empty() {
            credit = strings(&row["artista"]);
        }
        let artist: Vec<String> = credit.iter().map(|v| normalized(v)).collect();
        let album = strings(&row["album"]);
        if !artist.is_empty() && album.len() == 1 {
            groups
                .entry((artist, normalized(&album_base(&album[0]))))
                .or_default()
                .push(row);
        }
    }
    let mut variants = Vec::new();
    for ((artist, base), members) in &groups {
        let albums: HashSet<Vec<String>> =
            members.iter().map(|row| strings(&row["album"])).collect();
        if albums.len() < 2 {
            continue;
        }
        for row in members {
            variants.push(json!({
                "artista_grupo": artist,
                "chave_album_candidato": base,
                "album_base_candidato": album_base(&strings(&row["album"])[0]),
                "album_atual": row["album"],
                "titulo": row["titulo"],
                "faixa": row["faixa"],
                "disco": row["disco"],
                "arquivo": row["arquivo"],
                "marcadores_versao": row["marcadores_versao"],
                "status": tr(language, "review required; recording not identified"),
                "evidencia": tr(language, "text similarity; does not confirm Release Group, Release or Recording"),
            }));
        }
    }

    let mut formats = Map::new();
    for row in &tracks {
        let key = row["formato"].as_str().unwrap_or_default().to_string();
        let count = formats.get(&key).and_then(Value::as_u64).unwrap_or(0);
        formats.insert(key, json!(count + 1));
    }
    let mut missing = Map::new();
    for field in REQUIRED {
        let count = tracks
            .iter()
            .filter(|row| strings(&row["campos_ausentes"]).iter().any(|f| f == field))
            .count();
        missing.insert(field.to_string(), json!(count));
    }
    let filled = |key: &str| {
        tracks
            .iter()
            .filter(|row| match &row[key] {
                Value::Array(items) => !items.is_empty(),
                Value::Object(map) => !map.is_empty(),
                _ => false,
            })
            .count()
    };
    let group_count = variants
        .iter()
        .map(|r| {
            (
                r["artista_grupo"].to_string(),
                r["chave_album_candidato"].to_string(),
            )
        })
        .collect::<HashSet<_>>()
        .len();
    let limits: Vec<&str> = [
        "The inventory is not a restorable backup of the metadata.",
        "Stability compares size, mtime, ctime, device and inode; it does not hash the full audio.",
        "Reading may update atime, depending on the file system configuration.",
        "Version markers and album groups are heuristics for human review.",
        "It does not identify recordings, detect duplicates or approve changes.",
        "This program made no external queries.",
    ]
    .into_iter()
    .map(|text| tr(language, text))
    .collect();
    let summary = json!({
        "gerado_em_utc": now_iso(),
        "biblioteca": root.to_string_lossy(),
        "encontrados": files.len(),
        "lidos": tracks.len(),
        "formatos": formats,
        "campos_ausentes": missing,
        "com_capa": filled("capas"),
        "com_identificadores_musicbrainz": filled("musicbrainz"),
        "com_marcadores_de_versao": filled("marcadores_versao"),
        "grupos_candidatos_de_album": group_count,
        "erros": errors,
        "ignorados": skipped,
        "arquivos_com_mudanca_detectada": changed,
        "somente_leitura": true,
        "limites": limits,
    });

    fs::create_dir_all(output.parent().unwrap_or(Path::new("/")))?;
    fs::create_dir(output)?;
    let mut inventory = fs::File::create(output.join("inventario.json"))?;
    writeln!(
        inventory,
        "{}",
        pretty(&json!({"resumo": summary, "musicas": tracks}))
    )?;
    fs::write(output.join("resumo.json"), pretty(&summary) + "\n")?;
    let mut columns = vec!["arquivo", "formato"];
    columns.extend(FIELDS.iter().map(|(field, _, _)| *field));
    columns.extend([
        "duracao_segundos",
        "bitrate_bps",
        "sample_rate_hz",
        "canais",
        "bits_por_amostra",
        "formato_tags",
        "capas",
        "musicbrainz",
        "campos_ausentes",
        "marcadores_versao",
        "arquivo_estavel_ate_fim_varredura",
    ]);
    write_csv(&output.join("inventario.csv"), &tracks, &columns)?;
    write_csv(
        &output.join("variantes_albuns.csv"),
        &variants,
        &[
            "artista_grupo",
            "chave_album_candidato",
            "album_base_candidato",
            "album_atual",
            "titulo",
            "faixa",
            "disco",
            "arquivo",
            "marcadores_versao",
            "status",
            "evidencia",
        ],
    )?;
    write_csv(
        &output.join("erros.csv"),
        summary["erros"].as_array().unwrap(),
        &["arquivo", "erro"],
    )?;
    Ok(summary)
}
