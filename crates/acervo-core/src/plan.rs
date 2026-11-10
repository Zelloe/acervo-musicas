//! Generation of immutable plans and approval spreadsheets: the same files as
//! `acervo.library.plan.create_plan`. Reads only; the plan is applied elsewhere.

use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use regex::Regex;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::inventory::{
    album_base, normalized, now_iso, pretty, python_json, strings, walk, write_csv, Walk,
};
use crate::messages::{tr, Language};
use crate::scan::{frames_v24, read_track, signature, Parsed, REQUIRED};
use crate::{id3v2, protections};

pub const ORIGINAL_MP3: &str = "BibliotecaMusical:AlbumAnterior";
pub const ORIGINAL_FLAC: &str = "BIBLIOTECA_ALBUM_ANTERIOR";

pub type Rule = BTreeMap<String, String>;

/// `list(csv.DictReader(...))` for a UTF-8 file with or without BOM.
pub fn rules(path: &Path) -> io::Result<Vec<Rule>> {
    let text = fs::read_to_string(path)?;
    let mut rows = parse_csv(text.strip_prefix('\u{feff}').unwrap_or(&text)).into_iter();
    let header = rows.next().unwrap_or_default();
    Ok(rows
        .filter(|row| !row.is_empty())
        .map(|row| header.iter().cloned().zip(row).collect())
        .collect())
}

/// Rows of an Excel-style CSV: quoted fields may hold commas, quotes ("") and line breaks.
fn parse_csv(text: &str) -> Vec<Vec<String>> {
    let (mut rows, mut row, mut field) = (Vec::new(), Vec::new(), String::new());
    let (mut quoted, mut touched) = (false, false);
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '"' if quoted && chars.peek() == Some(&'"') => {
                chars.next();
                field.push('"');
            }
            '"' if quoted => quoted = false,
            '"' if field.is_empty() => (quoted, touched) = (true, true),
            ',' if !quoted => {
                row.push(std::mem::take(&mut field));
                touched = true;
            }
            '\r' | '\n' if !quoted => {
                if c == '\r' && chars.peek() == Some(&'\n') {
                    chars.next();
                }
                if touched || !field.is_empty() {
                    row.push(std::mem::take(&mut field));
                }
                rows.push(std::mem::take(&mut row));
                touched = false;
            }
            other => {
                field.push(other);
                touched = true;
            }
        }
    }
    if touched || !field.is_empty() {
        row.push(field);
    }
    if !row.is_empty() {
        rows.push(row);
    }
    rows
}

fn get<'a>(rule: &'a Rule, key: &str) -> &'a str {
    rule.get(key).map(String::as_str).unwrap_or("")
}

/// Value of a TXXX frame (MP3) or Vorbis comment (FLAC) that keeps the album's former name.
pub fn previous_album(path: &Path, language: Language) -> Result<Vec<String>, String> {
    if path.to_string_lossy().to_lowercase().ends_with(".mp3") {
        let tag = read_id3(path).map_err(|error| match error.as_str() {
            "Invalid ID3 header" => format!("ValueError: {}", tr(language, "Invalid ID3 header")),
            _ => error,
        })?;
        let Some(tag) = tag else {
            return Ok(Vec::new());
        };
        let key = format!("TXXX:{ORIGINAL_MP3}");
        return Ok(frames_v24(&tag)
            .into_iter()
            .filter(|(found, _)| *found == key)
            .flat_map(|(_, frame)| match frame {
                Parsed::Extended { values, .. } => values,
                _ => Vec::new(),
            })
            .collect());
    }
    let tag = metaflac::Tag::read_from_path(path).map_err(|e| format!("error: {e}"))?;
    Ok(tag
        .vorbis_comments()
        .map(|comments| {
            comments
                .comments
                .iter()
                .filter(|(key, _)| key.eq_ignore_ascii_case(ORIGINAL_FLAC))
                .flat_map(|(_, values)| values.clone())
                .collect()
        })
        .unwrap_or_default())
}

/// `read_id3`: no tag is an empty v2.4 tag; a broken one is an error.
fn read_id3(path: &Path) -> Result<Option<id3v2::Tag>, String> {
    match id3v2::read(path) {
        Ok(tag) => Ok(tag),
        Err(error) => {
            let mut head = [0u8; 3];
            let starts = fs::File::open(path)
                .and_then(|mut f| f.read_exact(&mut head))
                .is_ok();
            if starts && &head == b"ID3" {
                Err("Invalid ID3 header".into())
            } else {
                Err(format!("error: {error}"))
            }
        }
    }
}

/// The checks `core.integrity` makes before a file may be offered for writing.
fn integrity(path: &Path) -> Result<(), &'static str> {
    let name = path.to_string_lossy().to_lowercase();
    if name.ends_with(".mp3") {
        let tag = read_id3(path).map_err(|_| "Invalid ID3 header")?;
        if tag.is_some_and(|tag| !matches!(tag.major, 3 | 4)) {
            return Err("Writing is limited to ID3v2.3/v2.4; file sent for review");
        }
        return Ok(());
    }
    if !name.ends_with(".flac") {
        return Err("Unsupported format");
    }
    let mut file = fs::File::open(path).map_err(|_| "Invalid FLAC header")?;
    let mut head = [0u8; 10];
    let read = file.read(&mut head).map_err(|_| "Invalid FLAC header")?;
    let offset = if read == 10 && &head[..3] == b"ID3" {
        10 + head[6..]
            .iter()
            .fold(0u64, |acc, b| (acc << 7) | (*b & 0x7F) as u64)
    } else {
        0
    };
    let mut marker = [0u8; 4];
    file.seek(SeekFrom::Start(offset))
        .and_then(|_| file.read_exact(&mut marker))
        .map_err(|_| "Invalid FLAC header")?;
    if &marker != b"fLaC" {
        return Err("Invalid FLAC header");
    }
    let total = file.metadata().map_err(|_| "Truncated FLAC block")?.len();
    let mut at = offset + 4;
    loop {
        let mut header = [0u8; 4];
        file.read_exact(&mut header)
            .map_err(|_| "Truncated FLAC block")?;
        let size = u32::from_be_bytes([0, header[1], header[2], header[3]]) as u64;
        at += 4 + size;
        if at > total {
            return Err("Truncated FLAC block");
        }
        file.seek(SeekFrom::Start(at))
            .map_err(|_| "Truncated FLAC block")?;
        if header[0] & 0x80 != 0 {
            return Ok(());
        }
    }
}

pub fn sha256_file(path: &Path) -> io::Result<String> {
    let mut file = fs::File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0u8; 1024 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn sha256_text(text: &str) -> String {
    format!("{:x}", Sha256::digest(text.as_bytes()))
}

/// `json.dumps(value, sort_keys=True, ensure_ascii=False)`.
fn python_json_sorted(value: &Value) -> String {
    match value {
        Value::Object(map) => {
            let sorted: BTreeMap<&String, &Value> = map.iter().collect();
            let items: Vec<String> = sorted
                .into_iter()
                .map(|(k, v)| format!("{}: {}", Value::from(k.as_str()), python_json_sorted(v)))
                .collect();
            format!("{{{}}}", items.join(", "))
        }
        Value::Array(items) => {
            format!(
                "[{}]",
                items
                    .iter()
                    .map(python_json_sorted)
                    .collect::<Vec<_>>()
                    .join(", ")
            )
        }
        other => python_json(other),
    }
}

/// Suggested changes and review notes for one track.
pub fn propose(
    track: &Value,
    genres: &[Rule],
    albums: &[Rule],
    protected: &HashSet<String>,
    language: Language,
) -> Result<(Vec<Value>, Vec<String>), String> {
    let (mut changes, mut review) = (Vec::new(), Vec::new());
    for field in ["album", "genero"] {
        if protected.contains(field) {
            review.push(tr(language, "Protected field: {field}").replace("{field}", field));
        }
    }
    let mut artists = strings(&track["artista_album"]);
    if artists.is_empty() {
        artists = strings(&track["artista"]);
    }
    // A string with '/' is a single credit; AC/DC and featured artists are not split.
    let artist = if artists.len() == 1 {
        artists[0].clone()
    } else {
        String::new()
    };
    let track_albums = strings(&track["album"]);
    let genre_rules: Vec<&Rule> = genres
        .iter()
        .filter(|r| {
            normalized(get(r, "artista")) == normalized(&artist)
                && (get(r, "album").is_empty()
                    || track_albums
                        .iter()
                        .any(|a| normalized(&album_base(a)) == normalized(get(r, "album"))))
        })
        .collect();
    let specific: Vec<&Rule> = genre_rules
        .iter()
        .copied()
        .filter(|r| !get(r, "album").is_empty())
        .collect();
    let matches = if specific.is_empty() {
        &genre_rules
    } else {
        &specific
    };
    let existing_genre = strings(&track["genero"])
        .iter()
        .any(|v| !v.trim().is_empty());
    // Protection also applies to an empty field.
    if !existing_genre && !protected.contains("genero") {
        let options: HashSet<&str> = matches
            .iter()
            .map(|r| get(r, "genero"))
            .filter(|g| !g.trim().is_empty())
            .collect();
        if options.len() == 1 {
            let criterion = if specific.is_empty() {
                "Rule for the exact artist credit; requires approval"
            } else {
                "Album rule"
            };
            changes.push(json!({
                "campo": "genero",
                "atual": track["genero"],
                "proposto": [options.into_iter().next().unwrap()],
                "fonte": get(matches[0], "origem"),
                "criterio": tr(language, criterion),
            }));
        } else {
            review.push(tr(language, "Genre has no rule or conflicting rules").to_string());
        }
    }

    static REMASTER: OnceLock<Regex> = OnceLock::new();
    let remaster = REMASTER.get_or_init(|| {
        Regex::new(r"(?i)\s*-\s*remaster(?:ed)?(?:\s+\d{4})?\s*$").expect("valid pattern")
    });
    let first = |key: &str| {
        let values = strings(&track[key]);
        if values.len() == 1 {
            values[0].clone()
        } else {
            String::new()
        }
    };
    let rules_for_album: &[Rule] = if protected.contains("album") {
        &[]
    } else {
        albums
    };
    for rule in rules_for_album {
        if strings(&track["artista"]) != [get(rule, "artista")]
            || track_albums != [get(rule, "album_atual")]
        {
            continue;
        }
        if track_albums == [get(rule, "album_padrao")] {
            continue;
        }
        let title = first("titulo");
        let clean_title = remaster.replace_all(&title, "").into_owned();
        let numbers: Value = serde_json::from_str(get(rule, "faixas_json"))
            .map_err(|e| format!("JSONDecodeError: {e}"))?;
        let number = first("faixa").split('/').next().unwrap_or("").to_string();
        let disc = first("disco").split('/').next().unwrap_or("").to_string();
        let expected = numbers.get(&number).and_then(Value::as_str).unwrap_or("");
        // The whole title must match: "To Live Is to Die" is not a live version.
        if disc != get(rule, "disco")
            || normalized(&clean_title) != normalized(expected)
            || clean_title.is_empty()
        {
            review.push(
                tr(
                    language,
                    "Candidate album with mismatched version/track/disc; keep and review",
                )
                .into(),
            );
            continue;
        }
        let path = PathBuf::from(track["arquivo"].as_str().unwrap_or_default());
        if !previous_album(&path, language)?.is_empty() {
            review.push(
                tr(
                    language,
                    "Album already has a previous name recorded; review needed",
                )
                .into(),
            );
            continue;
        }
        changes.push(json!({
            "campo": "album",
            "atual": track["album"],
            "proposto": [get(rule, "album_padrao")],
            "fonte": get(rule, "fonte"),
            "criterio": tr(language, "Artist, album, disc, track and title match; recording not confirmed"),
            "preservacao": tr(language, "Current name kept in BibliotecaMusical:AlbumAnterior (MP3) or BIBLIOTECA_ALBUM_ANTERIOR (FLAC)"),
        }));
    }
    Ok((changes, review))
}

#[derive(Default)]
struct Outcome {
    track: Option<Value>,
    planned: Option<Value>,
    flat: Vec<Value>,
    review: Option<Value>,
    error: Option<String>,
}

struct Context<'a> {
    genres: &'a [Rule],
    albums: &'a [Rule],
    policy: &'a protections::Policy,
    root: &'a Path,
    language: Language,
}

fn analyze(path: &Path, context: &Context) -> Outcome {
    let mut outcome = Outcome::default();
    if let Err(error) = analyze_into(path, context, &mut outcome) {
        outcome.error = Some(error);
    }
    outcome
}

fn analyze_into(path: &Path, context: &Context, outcome: &mut Outcome) -> Result<(), String> {
    let language = context.language;
    let value_error = |text: &'static str| format!("ValueError: {}", tr(language, text));
    let file = path.to_string_lossy().to_string();
    let before = signature(path).map_err(|e| crate::inventory::os_error(path, &e))?;
    let mut track = read_track(path).map_err(|e| crate::inventory::read_error(language, e))?;
    let digest = sha256_file(path).map_err(|e| crate::inventory::os_error(path, &e))?;
    let now = signature(path).map_err(|e| crate::inventory::os_error(path, &e))?;
    if now != before || track["arquivo_estavel_durante_leitura"] != json!(true) {
        return Err(value_error("File changed during the analysis"));
    }
    track["id"] = json!(&sha256_text(&file)[..24]);
    outcome.track = Some(track.clone());
    let protected = protections::protected(context.policy, context.root, path)
        .map_err(|e| format!("ValueError: {e}"))?;
    let (mut changes, review) =
        propose(&track, context.genres, context.albums, &protected, language)?;
    if !changes.is_empty() {
        // Checks support before offering write approval.
        integrity(path).map_err(value_error)?;
        if fs::metadata(path).map(|m| m.nlink()).unwrap_or(1) > 1 {
            return Err(value_error("File with hard links; approval blocked"));
        }
        let join = |key: &str| strings(&track[key]).join(" | ");
        for change in &mut changes {
            let id = sha256_text(&format!("{file}{digest}{}", python_json_sorted(change)));
            change["id"] = json!(&id[..24]);
            outcome.flat.push(json!({
                "id": change["id"],
                "arquivo": file,
                "titulo": join("titulo"),
                "artista": join("artista"),
                "campo": change["campo"],
                "atual": python_json(&change["atual"]),
                "proposto": python_json(&change["proposto"]),
                "fonte": change["fonte"],
                "criterio": change["criterio"],
                "preservacao": change.get("preservacao").cloned().unwrap_or(json!("")),
                "aprovar": "nao",
            }));
        }
        outcome.planned = Some(json!({
            "arquivo": file,
            "sha256": digest,
            "tamanho_bytes": before["tamanho_bytes"],
            "alteracoes": changes,
        }));
    }
    let missing = &track["campos_ausentes"];
    if !review.is_empty() || missing.as_array().is_some_and(|m| !m.is_empty()) {
        outcome.review =
            Some(json!({"arquivo": file, "campos_ausentes": missing, "observacoes": review}));
    }
    Ok(())
}

/// Like `atomic_json`: a temporary file in the same folder, synced, then renamed.
fn atomic_json(path: &Path, data: &Value) -> io::Result<()> {
    let folder = path.parent().unwrap_or(Path::new("."));
    let temp = folder.join(format!(
        ".json-{}-{}",
        std::process::id(),
        path.file_name().unwrap_or_default().to_string_lossy()
    ));
    let result = (|| {
        let mut handle = fs::File::create(&temp)?;
        handle.write_all(pretty(data).as_bytes())?;
        handle.write_all(b"\n")?;
        handle.sync_all()?;
        fs::rename(&temp, path)?;
        fs::File::open(folder)?.sync_all()
    })();
    let _ = fs::remove_file(&temp);
    result
}

#[derive(Debug)]
pub enum PlanError {
    Invalid(String),
    Io(io::Error),
}

impl From<io::Error> for PlanError {
    fn from(error: io::Error) -> Self {
        PlanError::Io(error)
    }
}

pub struct Options<'a> {
    pub normalize_albums: bool,
    pub genre_rules: &'a Path,
    pub album_rules: &'a Path,
    /// Program data folder, where the protected fields live; None means no protections.
    pub storage: Option<&'a Path>,
    pub language: Language,
}

pub fn create_plan(root: &Path, output: &Path, options: &Options) -> Result<Value, PlanError> {
    let language = options.language;
    let root = root.canonicalize()?;
    let policy = match options.storage {
        Some(storage) => protections::load(storage, &root, language).map_err(PlanError::Invalid)?,
        None => protections::empty(&root),
    };
    if !root.is_dir() || output.starts_with(&root) {
        return Err(PlanError::Invalid(
            tr(
                language,
                "The library must be a folder and the reports must be kept outside it",
            )
            .into(),
        ));
    }
    if output.exists() {
        return Err(PlanError::Invalid(
            tr(
                language,
                "The output folder already exists; choose another one",
            )
            .into(),
        ));
    }
    let genres = rules(options.genre_rules)?;
    let albums = if options.normalize_albums {
        rules(options.album_rules)?
    } else {
        Vec::new()
    };
    let mut found = Walk {
        files: Vec::new(),
        skipped: Vec::new(),
        errors: Vec::new(),
    };
    walk(&root, &mut found);
    let Walk {
        mut files,
        skipped,
        mut errors,
    } = found;
    files.sort();
    let ignored: Vec<Value> = skipped
        .into_iter()
        .map(|(path, _)| json!(path.to_string_lossy()))
        .collect();

    let context = Context {
        genres: &genres,
        albums: &albums,
        policy: &policy,
        root: &root,
        language,
    };
    let workers = std::thread::available_parallelism()
        .map_or(1, |n| n.get())
        .min(8);
    let chunk = files.len().div_ceil(workers).max(1);
    let outcomes: Vec<Outcome> = std::thread::scope(|scope| {
        let handles: Vec<_> = files
            .chunks(chunk)
            .map(|part| {
                let context = &context;
                scope.spawn(move || {
                    part.iter()
                        .map(|path| analyze(path, context))
                        .collect::<Vec<_>>()
                })
            })
            .collect();
        handles
            .into_iter()
            .flat_map(|handle| handle.join().expect("analysis thread"))
            .collect()
    });

    let (mut tracks, mut planned, mut flat, mut reviews) =
        (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    for (path, outcome) in files.iter().zip(outcomes) {
        tracks.extend(outcome.track);
        planned.extend(outcome.planned);
        flat.extend(outcome.flat);
        reviews.extend(outcome.review);
        if let Some(error) = outcome.error {
            errors.push(json!({"arquivo": path.to_string_lossy(), "erro": error}));
        }
    }

    let count = |items: &mut dyn Iterator<Item = String>| {
        let mut counts = Map::new();
        for key in items {
            let n = counts.get(&key).and_then(Value::as_u64).unwrap_or(0);
            counts.insert(key, json!(n + 1));
        }
        counts
    };
    let formats = count(
        &mut tracks
            .iter()
            .map(|t| t["formato"].as_str().unwrap_or_default().to_string()),
    );
    let mut missing = Map::new();
    for field in REQUIRED {
        let n = tracks
            .iter()
            .filter(|t| strings(&t["campos_ausentes"]).iter().any(|f| f == field))
            .count();
        missing.insert(field.to_string(), json!(n));
    }
    let by_field = count(&mut planned.iter().flat_map(|r| {
        r["alteracoes"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|c| c["campo"].as_str().unwrap_or_default().to_string())
    }));
    let summary = json!({
        "encontrados": files.len(),
        "lidos": tracks.len(),
        "formatos": formats,
        "campos_ausentes": missing,
        "com_capa": tracks.iter().filter(|t| t["capas"].as_array().is_some_and(|c| !c.is_empty())).count(),
        "arquivos_com_propostas": planned.len(),
        "alteracoes_propostas": flat.len(),
        "propostas_por_campo": by_field,
        "revisoes": reviews.len(),
        "erros": errors,
        "ignorados": ignored,
        "consultas_musicbrainz": 0,
    });
    let plan = json!({
        "schema": 1,
        "criado_em": now_iso(),
        "biblioteca": root.to_string_lossy(),
        "resumo": summary,
        "arquivos": planned,
    });
    fs::create_dir_all(output.parent().unwrap_or(Path::new("/")))?;
    fs::create_dir(output)?;
    atomic_json(&output.join("plano.json"), &plan)?;
    fs::write(
        output.join("plano.sha256"),
        sha256_file(&output.join("plano.json"))? + "\n",
    )?;
    atomic_json(
        &output.join("inventario.json"),
        &json!({"resumo": summary, "musicas": tracks}),
    )?;
    atomic_json(&output.join("resumo.json"), &summary)?;
    write_csv(
        &output.join("alteracoes.csv"),
        &flat,
        &[
            "id",
            "arquivo",
            "titulo",
            "artista",
            "campo",
            "atual",
            "proposto",
            "fonte",
            "criterio",
            "preservacao",
            "aprovar",
        ],
    )?;
    write_csv(
        &output.join("revisao.csv"),
        &reviews,
        &["arquivo", "campos_ausentes", "observacoes"],
    )?;
    write_csv(
        &output.join("erros.csv"),
        summary["erros"].as_array().unwrap(),
        &["arquivo", "erro"],
    )?;
    Ok(json!({
        "pasta_relatorios": output.to_string_lossy(),
        "resumo": summary,
        "plano": output.join("plano.json").to_string_lossy(),
    }))
}
