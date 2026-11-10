//! A reader for ID3v2.2, 2.3 and 2.4 tags, written from the ID3v2 specifications.
//!
//! Frames come back exactly as stored, upgraded to ID3v2.4 names, so callers see one model
//! for every version. Nothing here writes to the file.

use std::fs::File;
use std::io::{self, Read};
use std::path::Path;

pub struct Frame {
    pub id: String,
    pub data: Vec<u8>,
}

pub struct Tag {
    pub major: u8,
    pub frames: Vec<Frame>,
}

fn syncsafe(bytes: &[u8]) -> usize {
    bytes
        .iter()
        .fold(0, |total, b| (total << 7) | (*b as usize & 0x7F))
}

fn big_endian(bytes: &[u8]) -> usize {
    bytes.iter().fold(0, |total, b| (total << 8) | *b as usize)
}

/// Removes the 0x00 inserted after each 0xFF by unsynchronisation.
fn resync(data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len());
    let mut previous = 0u8;
    for &byte in data {
        if !(previous == 0xFF && byte == 0x00) {
            out.push(byte);
        }
        previous = byte;
    }
    out
}

/// ID3v2.2 three-letter names and their ID3v2.4 equivalents.
fn upgrade_v22(id: &str) -> String {
    let name = match id {
        "TT1" => "TIT1",
        "TT2" => "TIT2",
        "TT3" => "TIT3",
        "TP1" => "TPE1",
        "TP2" => "TPE2",
        "TP3" => "TPE3",
        "TP4" => "TPE4",
        "TAL" => "TALB",
        "TYE" => "TYER",
        "TOR" => "TORY",
        "TRK" => "TRCK",
        "TPA" => "TPOS",
        "TCO" => "TCON",
        "TCM" => "TCOM",
        "TXT" => "TEXT",
        "TLA" => "TLAN",
        "TLE" => "TLEN",
        "TBP" => "TBPM",
        "TEN" => "TENC",
        "TCR" => "TCOP",
        "TPB" => "TPUB",
        "TSS" => "TSSE",
        "TOA" => "TOPE",
        "TOT" => "TOAL",
        "TXX" => "TXXX",
        "PIC" => "APIC",
        "UFI" => "UFID",
        "COM" => "COMM",
        "ULT" => "USLT",
        "WXX" => "WXXX",
        "CNT" => "PCNT",
        "POP" => "POPM",
        "GEO" => "GEOB",
        "TDA" => "TDAT",
        "TIM" => "TIME",
        "TRD" => "TRDA",
        "TSI" => "TSIZ",
        "TDY" => "TDLY",
        "TFT" => "TFLT",
        "TKE" => "TKEY",
        "TMT" => "TMED",
        "TOF" => "TOFN",
        "TOL" => "TOLY",
        "TRC" => "TSRC",
        "TCP" => "TCMP",
        "TS2" => "TSO2",
        "TSA" => "TSOA",
        "TSC" => "TSOC",
        "TSP" => "TSOP",
        "TST" => "TSOT",
        other => return other.to_string(),
    };
    name.to_string()
}

pub fn read(path: &Path) -> io::Result<Option<Tag>> {
    let mut file = File::open(path)?;
    let mut header = [0u8; 10];
    if file.read(&mut header)? < 10 || &header[..3] != b"ID3" {
        return Ok(None);
    }
    let major = header[3];
    if !(2..=4).contains(&major) {
        return Ok(None);
    }
    let flags = header[5];
    let size = syncsafe(&header[6..10]);
    let mut body = vec![0u8; size];
    let read = file.read(&mut body)?;
    body.truncate(read);
    if major < 4 && flags & 0x80 != 0 {
        body = resync(&body);
    }
    let mut at = 0;
    if flags & 0x40 != 0 && major >= 3 && body.len() >= 4 {
        at = if major == 4 {
            syncsafe(&body[..4])
        } else {
            4 + big_endian(&body[..4])
        };
    }
    let header_size = if major == 2 { 6 } else { 10 };
    let mut frames = Vec::new();
    while at + header_size <= body.len() {
        let raw = &body[at..at + header_size];
        let id_len = if major == 2 { 3 } else { 4 };
        let id = &raw[..id_len];
        if !id
            .iter()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit())
        {
            break; // padding or garbage after the last frame
        }
        let size = match major {
            2 => big_endian(&raw[3..6]),
            3 => big_endian(&raw[4..8]),
            _ => syncsafe(&raw[4..8]),
        };
        let frame_flags = if major == 2 {
            0
        } else {
            big_endian(&raw[8..10])
        };
        let start = at + header_size;
        if size == 0 || start + size > body.len() {
            break;
        }
        let mut data = body[start..start + size].to_vec();
        at = start + size;
        let id = String::from_utf8_lossy(id).into_owned();
        if major == 3 {
            if frame_flags & 0x0080 != 0 || frame_flags & 0x0040 != 0 {
                continue; // compressed or encrypted frames are left out
            }
            if frame_flags & 0x0020 != 0 && !data.is_empty() {
                data.remove(0);
            }
        } else if major == 4 {
            if frame_flags & 0x0008 != 0 || frame_flags & 0x0004 != 0 {
                continue;
            }
            if frame_flags & 0x0040 != 0 && !data.is_empty() {
                data.remove(0);
            }
            if frame_flags & 0x0001 != 0 && data.len() >= 4 {
                data.drain(..4);
            }
            if frame_flags & 0x0002 != 0 || flags & 0x80 != 0 {
                data = resync(&data);
            }
        }
        let id = if major == 2 { upgrade_v22(&id) } else { id };
        frames.push(Frame { id, data });
    }
    Ok(Some(Tag { major, frames }))
}

/// Splits encoded text on its terminator and decodes each part.
pub fn decode_strings(encoding: u8, data: &[u8]) -> Vec<String> {
    let wide = encoding == 1 || encoding == 2;
    let mut parts = Vec::new();
    let mut start = 0;
    let mut i = 0;
    while i < data.len() {
        let end = if wide {
            i + 1 < data.len() && i % 2 == start % 2 && data[i] == 0 && data[i + 1] == 0
        } else {
            data[i] == 0
        };
        if end {
            parts.push(&data[start..i]);
            i += if wide { 2 } else { 1 };
            start = i;
        } else {
            i += 1;
        }
    }
    parts.push(&data[start..]);
    while parts.len() > 1 && parts.last().is_some_and(|p| p.is_empty()) {
        parts.pop();
    }
    parts.iter().map(|part| decode(encoding, part)).collect()
}

pub fn decode(encoding: u8, bytes: &[u8]) -> String {
    match encoding {
        0 => bytes.iter().map(|&b| b as char).collect(),
        1 | 2 => {
            let (big, body) = match bytes {
                [0xFE, 0xFF, rest @ ..] => (true, rest),
                [0xFF, 0xFE, rest @ ..] => (false, rest),
                _ => (encoding == 2, bytes),
            };
            let units: Vec<u16> = body
                .as_chunks::<2>()
                .0
                .iter()
                .map(|c| {
                    if big {
                        u16::from_be_bytes(*c)
                    } else {
                        u16::from_le_bytes(*c)
                    }
                })
                .collect();
            String::from_utf16_lossy(&units)
        }
        _ => String::from_utf8_lossy(bytes).into_owned(),
    }
}

/// Position after the first terminator of `encoding` that starts at `from`.
pub fn after_terminator(encoding: u8, data: &[u8], from: usize) -> (usize, usize) {
    let wide = encoding == 1 || encoding == 2;
    let mut i = from;
    while i < data.len() {
        if wide {
            if i + 1 < data.len() && data[i] == 0 && data[i + 1] == 0 {
                return (i, i + 2);
            }
            i += 2;
        } else {
            if data[i] == 0 {
                return (i, i + 1);
            }
            i += 1;
        }
    }
    (data.len(), data.len())
}
