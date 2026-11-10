//! Read-only inventory of one track: the same fields as `acervo.library.scan.read_track`.

use std::fs;
use std::io::{self, Read, Seek, SeekFrom};
use std::os::unix::fs::MetadataExt;
use std::path::Path;
use std::sync::OnceLock;

use regex::Regex;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::id3v2::{after_terminator, decode, decode_strings};
use crate::{genres, id3v2, mp3info};

/// Inventory field, ID3v2 frame, Vorbis comment.
pub const FIELDS: [(&str, &str, &str); 9] = [
    ("titulo", "TIT2", "title"),
    ("artista", "TPE1", "artist"),
    ("artista_album", "TPE2", "albumartist"),
    ("album", "TALB", "album"),
    ("ano", "TDRC", "date"),
    ("ano_original", "TDOR", "originaldate"),
    ("faixa", "TRCK", "tracknumber"),
    ("disco", "TPOS", "discnumber"),
    ("genero", "TCON", "genre"),
];
pub const REQUIRED: [&str; 8] = [
    "titulo",
    "artista",
    "artista_album",
    "album",
    "ano",
    "faixa",
    "disco",
    "genero",
];

/// Words that suggest a version other than the studio one (live, demo, remix...).
pub fn version_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(
        r"(?i)\b(live|ao vivo|demo|remix|alternate|alternative mix|mix(?:es)?|session|bonus|acoustic|ac[uú]stic[ao]|instrumental|rehearsal|ensaio|outtake|edit|version|vers[aã]o)\b",
    )
        .expect("valid pattern")
    })
}

pub fn signature(path: &Path) -> std::io::Result<Value> {
    let meta = fs::metadata(path)?;
    Ok(json!({
        "tamanho_bytes": meta.size(),
        "mtime_ns": meta.mtime() as i128 * 1_000_000_000 + meta.mtime_nsec() as i128,
        "ctime_ns": meta.ctime() as i128 * 1_000_000_000 + meta.ctime_nsec() as i128,
        "dispositivo": meta.dev(),
        "inode": meta.ino(),
    }))
}

fn picture(data: &[u8], mime: &str, kind: u8, description: &str) -> Value {
    json!({
        "tamanho_bytes": data.len(),
        "sha256": format!("{:x}", Sha256::digest(data)),
        "mime": mime,
        "tipo": kind,
        "descricao": description,
    })
}

enum Kind {
    Mp3,
    Flac,
}

/// Picks the reader the way `mutagen.File` scores candidates: the header and the extension
/// both count, and a tie goes to MP3.
fn kind(path: &Path) -> io::Result<Option<Kind>> {
    let mut head = [0u8; 4];
    let read = fs::File::open(path)?.read(&mut head)?;
    let head = &head[..read];
    let name = path.to_string_lossy().to_lowercase();
    let mp3 = 2 * u8::from(head.starts_with(b"ID3")) + u8::from(name.ends_with(".mp3"));
    let flac = u8::from(head == b"fLaC") + 3 * u8::from(name.ends_with(".flac"));
    Ok(match (mp3, flac) {
        (0, 0) => None,
        (m, f) if m >= f => Some(Kind::Mp3),
        _ => Some(Kind::Flac),
    })
}

/// mutagen's error for a FLAC file it cannot open, as "Type: message".
fn flac_error(path: &Path) -> Option<String> {
    let mut head = Vec::new();
    fs::File::open(path)
        .ok()?
        .take(4)
        .read_to_end(&mut head)
        .ok()?;
    if head.len() < 4 {
        return Some(format!(
            "error: file said 4 bytes, read {} bytes",
            head.len()
        ));
    }
    if head != b"fLaC" && !head.starts_with(b"ID3") {
        return Some(format!(
            "FLACNoHeaderError: '{}' is not a valid FLAC file",
            path.display()
        ));
    }
    None
}

/// One frame, decoded far enough to read the inventory fields.
pub enum Parsed {
    Text(Vec<String>),
    Extended {
        description: String,
        values: Vec<String>,
    },
    Picture {
        mime: String,
        kind: u8,
        description: String,
        data: Vec<u8>,
    },
    Ufid {
        owner: String,
        data: Vec<u8>,
    },
    Other,
}

fn latin1_until_zero(data: &[u8], from: usize) -> (String, usize) {
    let (end, next) = after_terminator(0, data, from);
    (decode(0, &data[from.min(data.len())..end]), next)
}

/// The frame and the key that identifies it: frames with the same key are one frame.
fn parse(frame: &id3v2::Frame, major: u8) -> (String, Parsed) {
    let data = &frame.data;
    let id = frame.id.as_str();
    let encoding = data.first().copied().unwrap_or(0);
    let body = data.get(1..).unwrap_or(&[]);
    match id {
        "TXXX" => {
            let (end, next) = after_terminator(encoding, body, 0);
            let description = decode(encoding, &body[..end]);
            let values = decode_strings(encoding, &body[next..]);
            (
                format!("TXXX:{description}"),
                Parsed::Extended {
                    description,
                    values,
                },
            )
        }
        _ if id.starts_with('T') => (id.to_string(), Parsed::Text(decode_strings(encoding, body))),
        "APIC" => {
            let (mime, next) = if major == 2 {
                let format = decode(0, body.get(..3).unwrap_or(&[])).to_uppercase();
                let mime = match format.as_str() {
                    "JPG" => "image/jpeg".to_string(),
                    "PNG" => "image/png".to_string(),
                    other => format!("image/{}", other.to_lowercase()),
                };
                (mime, 3)
            } else {
                latin1_until_zero(body, 0)
            };
            let kind = body.get(next).copied().unwrap_or(0);
            let (end, after) = after_terminator(encoding, body, next + 1);
            let description = decode(encoding, &body[(next + 1).min(body.len())..end]);
            let picture = body.get(after..).unwrap_or(&[]).to_vec();
            (
                format!("APIC:{description}"),
                Parsed::Picture {
                    mime,
                    kind,
                    description,
                    data: picture,
                },
            )
        }
        "UFID" => {
            let (owner, next) = latin1_until_zero(data, 0);
            let value = data.get(next..).unwrap_or(&[]).to_vec();
            (format!("UFID:{owner}"), Parsed::Ufid { owner, data: value })
        }
        "COMM" | "USLT" => {
            let language = decode(0, body.get(..3).unwrap_or(&[]));
            let (end, _) = after_terminator(encoding, body, 3);
            let description = decode(encoding, &body[3.min(body.len())..end]);
            (format!("{id}:{description}:{language}"), Parsed::Other)
        }
        "WXXX" | "GEOB" => {
            let (end, _) = after_terminator(encoding, body, 0);
            (
                format!("{id}:{}", decode(encoding, &body[..end])),
                Parsed::Other,
            )
        }
        "POPM" | "PRIV" | "RVA2" => (
            format!("{id}:{}", latin1_until_zero(data, 0).0),
            Parsed::Other,
        ),
        _ => (id.to_string(), Parsed::Other),
    }
}

/// Frames in file order, merged like one ID3v2.4 tag: repeated text frames add their
/// values, any other repeated frame keeps the last one, and old date frames become TDRC/TDOR.
pub fn frames_v24(tag: &id3v2::Tag) -> Vec<(String, Parsed)> {
    let mut frames: Vec<(String, Parsed)> = Vec::new();
    for frame in &tag.frames {
        let (key, parsed) = parse(frame, tag.major);
        match frames.iter_mut().find(|(existing, _)| *existing == key) {
            Some((_, Parsed::Text(values))) => {
                if let Parsed::Text(more) = parsed {
                    values.extend(more);
                }
            }
            Some((_, slot)) => *slot = parsed,
            None => frames.push((key, parsed)),
        }
    }
    let text = |frames: &[(String, Parsed)], key: &str| -> Option<String> {
        frames
            .iter()
            .find(|(k, _)| k == key)
            .and_then(|(_, p)| match p {
                Parsed::Text(values) => values.first().cloned(),
                _ => None,
            })
    };
    if tag.major < 4 {
        if let Some(year) = text(&frames, "TYER") {
            let mut date = year;
            if let Some(day) = text(&frames, "TDAT").filter(|d| d.len() == 4) {
                date = format!("{date}-{}-{}", &day[2..], &day[..2]);
                if let Some(time) = text(&frames, "TIME").filter(|t| t.len() == 4) {
                    date = format!("{date}T{}:{}:00", &time[..2], &time[2..]);
                }
            }
            if !frames.iter().any(|(k, _)| k == "TDRC") {
                frames.push(("TDRC".into(), Parsed::Text(vec![date])));
            }
        }
        if let Some(year) = text(&frames, "TORY") {
            if !frames.iter().any(|(k, _)| k == "TDOR") {
                frames.push(("TDOR".into(), Parsed::Text(vec![year])));
            }
        }
        frames.retain(|(k, _)| !matches!(k.as_str(), "TYER" | "TDAT" | "TIME" | "TORY"));
    }
    frames
}

pub fn read_track(path: &Path) -> Result<Value, String> {
    let before = signature(path).map_err(|e| e.to_string())?;
    let kind = kind(path)
        .map_err(|e| e.to_string())?
        .ok_or("File not recognized as MP3/FLAC")?;
    let mut row = Map::new();
    let (format, tag_format, covers, ids, keys, length, bitrate, sample_rate, channels, bits);
    match kind {
        Kind::Mp3 => {
            let info = mp3info::read(path)
                .map_err(|_| "HeaderNotFoundError: can't sync to MPEG frame".to_string())?;
            let tag = id3v2::read(path).map_err(|e| e.to_string())?;
            let frames = tag.as_ref().map(frames_v24).unwrap_or_default();
            for (field, frame_id, _) in FIELDS {
                let values = match frames.iter().find(|(key, _)| key == frame_id) {
                    Some((_, Parsed::Text(values))) if frame_id == "TCON" => {
                        values.iter().flat_map(|v| genres::expand(v)).collect()
                    }
                    Some((_, Parsed::Text(values))) => values.clone(),
                    _ => Vec::new(),
                };
                row.insert(field.into(), json!(values));
            }
            let mut found_covers = Vec::new();
            let mut found_ids = Map::new();
            for (_, frame) in &frames {
                match frame {
                    Parsed::Picture {
                        mime,
                        kind,
                        description,
                        data,
                    } => found_covers.push(picture(data, mime, *kind, description)),
                    Parsed::Extended {
                        description,
                        values,
                    } if description.to_lowercase().contains("musicbrainz") => {
                        found_ids.insert(description.clone(), json!(values));
                    }
                    Parsed::Ufid { owner, data }
                        if owner.to_lowercase().contains("musicbrainz") =>
                    {
                        found_ids.insert(
                            format!("UFID:{owner}"),
                            json!([String::from_utf8_lossy(data)]),
                        );
                    }
                    _ => {}
                }
            }
            let mut found_keys: Vec<String> = frames.iter().map(|(key, _)| key.clone()).collect();
            found_keys.sort();
            format = "MP3";
            tag_format = tag.as_ref().map(|t| match t.major {
                2 => "2.2.0",
                3 => "2.3.0",
                _ => "2.4.0",
            });
            covers = found_covers;
            ids = found_ids;
            keys = found_keys;
            length = info.length;
            bitrate = Some(info.bitrate);
            sample_rate = info.sample_rate;
            channels = info.channels;
            bits = None;
        }
        Kind::Flac => {
            if let Some(error) = flac_error(path) {
                return Err(error);
            }
            let tag = metaflac::Tag::read_from_path(path).map_err(|e| format!("error: {e}"))?;
            let info = tag.get_streaminfo().ok_or("FLAC without STREAMINFO")?;
            let comments = tag.vorbis_comments();
            let lookup = |name: &str| -> Vec<String> {
                comments
                    .map(|c| {
                        c.comments
                            .iter()
                            .filter(|(key, _)| key.eq_ignore_ascii_case(name))
                            .flat_map(|(_, values)| values.clone())
                            .collect()
                    })
                    .unwrap_or_default()
            };
            for (field, _, vorbis) in FIELDS {
                row.insert(field.into(), json!(lookup(vorbis)));
            }
            covers = tag
                .pictures()
                .map(|p| picture(&p.data, &p.mime_type, p.picture_type as u8, &p.description))
                .collect();
            let mut found_ids = Map::new();
            let mut found_keys = Vec::new();
            if let Some(c) = comments {
                for (key, values) in &c.comments {
                    let key = key.to_lowercase();
                    if key.contains("musicbrainz") {
                        found_ids.insert(key.clone(), json!(values));
                    }
                    found_keys.push(key);
                }
            }
            found_keys.sort();
            ids = found_ids;
            keys = found_keys;
            format = "FLAC";
            tag_format = Some("Vorbis Comment");
            length = info.total_samples as f64 / info.sample_rate as f64;
            // Like mutagen, only the audio after the metadata blocks counts.
            let size = fs::metadata(path).map_err(|e| e.to_string())?.len();
            let audio = size.saturating_sub(flac_audio_offset(path).map_err(|e| e.to_string())?);
            bitrate = if length > 0.0 {
                Some((audio as f64 * 8.0 / length) as u32)
            } else {
                None
            };
            sample_rate = info.sample_rate;
            channels = info.num_channels as u32;
            bits = Some(info.bits_per_sample as u32);
        }
    }
    let missing: Vec<&str> = REQUIRED
        .iter()
        .copied()
        .filter(|field| {
            !row[*field]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| !v.as_str().unwrap().trim().is_empty())
        })
        .collect();
    let text = |field: &str| {
        row[field]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect::<Vec<_>>()
    };
    let joined = [text("titulo"), text("album")].concat().join(" ");
    let mut markers: Vec<String> = version_pattern()
        .find_iter(&joined)
        .map(|m| m.as_str().to_string())
        .collect();
    markers.sort();
    markers.dedup();
    row.insert("arquivo".into(), json!(path.to_string_lossy()));
    row.insert("formato".into(), json!(format));
    row.insert("formato_tags".into(), json!(tag_format));
    row.insert("assinatura_antes".into(), before.clone());
    row.insert(
        "duracao_segundos".into(),
        json!((length * 1e6).round() / 1e6),
    );
    row.insert("bitrate_bps".into(), json!(bitrate));
    row.insert("sample_rate_hz".into(), json!(sample_rate));
    row.insert("canais".into(), json!(channels));
    row.insert("bits_por_amostra".into(), json!(bits));
    row.insert("capas".into(), json!(covers));
    row.insert("musicbrainz".into(), Value::Object(ids));
    row.insert("chaves_tags".into(), json!(keys));
    row.insert("campos_ausentes".into(), json!(missing));
    row.insert("marcadores_versao".into(), json!(markers));
    let after = signature(path).map_err(|e| e.to_string())?;
    row.insert(
        "arquivo_estavel_durante_leitura".into(),
        json!(before == after),
    );
    Ok(Value::Object(row))
}

/// Where the FLAC frames start: after the "fLaC" marker and every metadata block.
fn flac_audio_offset(path: &Path) -> io::Result<u64> {
    let mut file = fs::File::open(path)?;
    let mut head = [0u8; 4];
    file.read_exact(&mut head)?;
    let mut offset = 4u64;
    if &head != b"fLaC" {
        // An ID3 tag in front of the FLAC stream.
        let mut rest = [0u8; 6];
        file.read_exact(&mut rest)?;
        let size = rest[2..]
            .iter()
            .fold(0u64, |acc, b| (acc << 7) | (*b & 0x7F) as u64);
        offset = 10 + size + 4;
        file.seek(SeekFrom::Start(offset))?;
    }
    loop {
        let mut block = [0u8; 4];
        file.read_exact(&mut block)?;
        let length = u32::from_be_bytes([0, block[1], block[2], block[3]]) as u64;
        offset += 4 + length;
        if block[0] & 0x80 != 0 {
            return Ok(offset);
        }
        file.seek(SeekFrom::Start(offset))?;
    }
}
