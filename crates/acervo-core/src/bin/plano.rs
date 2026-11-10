//! Creates an inventory and a preview plan; never modifies music. Same options as
//! `python -m acervo analyze`, except the MusicBrainz lookup, which is not ported yet.

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use acervo_core::inventory::pretty;
use acervo_core::messages::{environment_language, tr};
use acervo_core::plan::{create_plan, Options, PlanError};

/// The checkout this binary was built from: rules and, as in Python, the default data folder.
fn checkout() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn default_data() -> PathBuf {
    let checkout = checkout();
    if checkout.join("pyproject.toml").is_file() {
        return checkout.canonicalize().unwrap_or(checkout);
    }
    let base = std::env::var("XDG_DATA_HOME")
        .ok()
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(&std::env::var("HOME").unwrap_or_default()).join(".local/share")
        });
    base.join("acervo")
}

fn expand(value: &str) -> PathBuf {
    match value.strip_prefix("~/") {
        Some(rest) => Path::new(&std::env::var("HOME").unwrap_or_default()).join(rest),
        None => PathBuf::from(value),
    }
}

/// `Path.resolve()` for a folder that may not exist yet.
fn resolve(path: &Path) -> PathBuf {
    let path = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    if let Ok(real) = path.canonicalize() {
        return real;
    }
    match (path.parent(), path.file_name()) {
        (Some(parent), Some(name)) => resolve(parent).join(name),
        _ => path,
    }
}

fn main() -> ExitCode {
    let language = environment_language();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let value = |names: &[&str]| {
        args.iter()
            .position(|a| names.contains(&a.as_str()))
            .and_then(|i| args.get(i + 1))
            .map(|v| expand(v))
    };
    let Some(folder) = value(&["--folder", "--pasta"]) else {
        eprintln!("usage: plano --folder <library> [--output <new folder>] [--normalize-albums] [--genre-rules <csv>] [--album-rules <csv>] [--data <folder>]");
        return ExitCode::from(2);
    };
    if args.iter().any(|a| a == "--musicbrainz") {
        eprintln!("plano: --musicbrainz is not available in the Rust version yet");
        return ExitCode::from(2);
    }
    let output = value(&["--output", "--saida"]).unwrap_or_else(|| {
        let stamp = std::process::Command::new("date")
            .arg("+previa_%Y%m%d_%H%M%S")
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_else(|_| "previa".into());
        Path::new("relatorios").join(stamp)
    });
    let rules = checkout().join("src/acervo/data");
    let genre_rules =
        value(&["--genre-rules", "--generos"]).unwrap_or_else(|| rules.join("genre_rules.csv"));
    let album_rules =
        value(&["--album-rules", "--albuns"]).unwrap_or_else(|| rules.join("album_rules.csv"));
    let data = value(&["--data", "--dados"]).unwrap_or_else(default_data);
    let options = Options {
        normalize_albums: args
            .iter()
            .any(|a| a == "--normalize-albums" || a == "--normalizar-albuns"),
        genre_rules: &genre_rules,
        album_rules: &album_rules,
        storage: Some(&data),
        language,
    };
    match create_plan(&folder, &resolve(&output), &options) {
        Ok(result) => {
            println!("{}", pretty(&result));
            let errors = result["resumo"]["erros"]
                .as_array()
                .is_some_and(|e| !e.is_empty());
            ExitCode::from(if errors { 2 } else { 0 })
        }
        Err(error) => {
            let message = match error {
                PlanError::Invalid(message) => message,
                PlanError::Io(error) => error.to_string(),
            };
            eprintln!(
                "{}",
                tr(language, "Error: {error}").replace("{error}", &message)
            );
            ExitCode::from(1)
        }
    }
}
