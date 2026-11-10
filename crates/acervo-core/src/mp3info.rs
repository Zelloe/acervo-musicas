//! Duration and stream properties of an MP3 file, from the MPEG audio frame headers.

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;

pub struct Info {
    pub length: f64,
    pub bitrate: u32,
    pub sample_rate: u32,
    pub channels: u32,
}

const BITRATES_V1_L3: [u32; 16] = [
    0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0,
];
const BITRATES_V2_L3: [u32; 16] = [
    0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0,
];

struct Header {
    mpeg1: bool,
    bitrate: u32,
    sample_rate: u32,
    channels: u32,
    samples: u32,
    size: usize,
}

fn header(bytes: &[u8]) -> Option<Header> {
    if bytes.len() < 4 || bytes[0] != 0xFF || bytes[1] & 0xE0 != 0xE0 {
        return None;
    }
    let version = (bytes[1] >> 3) & 0b11; // 0: MPEG 2.5, 2: MPEG 2, 3: MPEG 1
    let layer = (bytes[1] >> 1) & 0b11; // 1: Layer III
    if version == 1 || layer != 1 {
        return None;
    }
    let mpeg1 = version == 3;
    let bitrate = if mpeg1 {
        BITRATES_V1_L3
    } else {
        BITRATES_V2_L3
    }[(bytes[2] >> 4) as usize]
        * 1000;
    let base = [44100, 48000, 32000, 0][((bytes[2] >> 2) & 0b11) as usize];
    let sample_rate = match version {
        3 => base,
        2 => base / 2,
        _ => base / 4,
    };
    if bitrate == 0 || sample_rate == 0 {
        return None;
    }
    let padding = ((bytes[2] >> 1) & 1) as usize;
    let channels = if bytes[3] >> 6 == 3 { 1 } else { 2 };
    let samples = if mpeg1 { 1152 } else { 576 };
    let size = (samples as usize / 8) * bitrate as usize / sample_rate as usize + padding;
    Some(Header {
        mpeg1,
        bitrate,
        sample_rate,
        channels,
        samples,
        size,
    })
}

fn id3v2_size(bytes: &[u8]) -> usize {
    if bytes.len() < 10 || &bytes[..3] != b"ID3" {
        return 0;
    }
    let size = bytes[6..10]
        .iter()
        .fold(0usize, |total, b| (total << 7) | (*b as usize & 0x7F));
    let footer = if bytes[5] & 0x10 != 0 { 10 } else { 0 };
    10 + size + footer
}

pub fn read(path: &Path) -> io::Result<Info> {
    let mut file = File::open(path)?;
    let total = file.metadata()?.len();
    let mut head = [0u8; 10];
    let read = file.read(&mut head)?;
    let mut start = id3v2_size(&head[..read]) as u64;
    // Some files carry more than one ID3v2 tag before the audio.
    loop {
        file.seek(SeekFrom::Start(start))?;
        let read = file.read(&mut head)?;
        let size = id3v2_size(&head[..read]);
        if size == 0 {
            break;
        }
        start += size as u64;
    }
    file.seek(SeekFrom::Start(start))?;
    let mut window = vec![0u8; 256 * 1024];
    let read = file.read(&mut window)?;
    window.truncate(read);
    // The first frame is the first header whose successor is also a valid header.
    let mut found = None;
    for offset in 0..window.len().saturating_sub(4) {
        if let Some(first) = header(&window[offset..]) {
            let next = offset + first.size;
            if next + 4 > window.len() || header(&window[next..]).is_some() {
                found = Some((offset, first));
                break;
            }
        }
    }
    let Some((offset, first)) = found else {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "no MPEG audio frame found",
        ));
    };
    let frame = &window[offset..];
    let side = match (first.mpeg1, first.channels) {
        (true, 1) => 17,
        (true, _) => 32,
        (false, 1) => 9,
        (false, _) => 17,
    };
    let mut frames = None;
    let mut trimmed = 0u64;
    let mut stream_bytes = None;
    let mut vbri = false;
    let xing = 4 + side;
    if frame.len() > xing + 16
        && (&frame[xing..xing + 4] == b"Xing" || &frame[xing..xing + 4] == b"Info")
    {
        let flags = u32::from_be_bytes(frame[xing + 4..xing + 8].try_into().unwrap());
        let mut at = xing + 8;
        if flags & 1 != 0 {
            frames = Some(u32::from_be_bytes(frame[at..at + 4].try_into().unwrap()) as u64);
            at += 4;
        }
        if flags & 2 != 0 && frame.len() >= at + 4 {
            stream_bytes = Some(u32::from_be_bytes(frame[at..at + 4].try_into().unwrap()) as u64);
            at += 4;
        }
        if flags & 4 != 0 {
            at += 100;
        }
        if flags & 8 != 0 {
            at += 4;
        }
        // The LAME extension stores how many samples the encoder added at each end. Other
        // encoders such as ffmpeg fill the same bytes, but only LAME's values are trusted.
        if frame.len() >= at + 24 && &frame[at..at + 4] == b"LAME" {
            let packed = &frame[at + 21..at + 24];
            let delay = ((packed[0] as u64) << 4) | (packed[1] as u64 >> 4);
            let padding = (((packed[1] & 0x0F) as u64) << 8) | packed[2] as u64;
            trimmed = delay + padding;
        }
    } else if frame.len() > 36 + 18 && &frame[36..40] == b"VBRI" {
        stream_bytes = Some(u32::from_be_bytes(frame[46..50].try_into().unwrap()) as u64);
        frames = Some(u32::from_be_bytes(frame[50..54].try_into().unwrap()) as u64);
        vbri = true;
    }
    // Same arithmetic as mutagen, so both readers report the same numbers.
    let sample_rate = first.sample_rate as f64;
    let (length, bitrate) = match frames {
        Some(count) if vbri => {
            let length = (count * first.samples as u64) as f64 / sample_rate;
            let bitrate = if length > 0.0 {
                (stream_bytes.unwrap_or(0) as f64 * 8.0 / length) as u32
            } else {
                first.bitrate
            };
            (length, bitrate)
        }
        Some(count) => {
            let samples = count * first.samples as u64;
            let bitrate = match stream_bytes {
                // The Xing frame itself is counted in the bytes but not in the frames.
                Some(bytes) if samples > 0 => {
                    let audio = bytes.saturating_sub(first.size as u64) as f64;
                    (audio * 8.0 * sample_rate / samples as f64).round() as u32
                }
                _ => first.bitrate,
            };
            (
                samples.saturating_sub(trimmed) as f64 / sample_rate,
                bitrate,
            )
        }
        None => {
            let content = total.saturating_sub(start + offset as u64);
            (content as f64 * 8.0 / first.bitrate as f64, first.bitrate)
        }
    };
    Ok(Info {
        length,
        bitrate,
        sample_rate: first.sample_rate,
        channels: first.channels,
    })
}
