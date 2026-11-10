//! Read-only diagnosis of the music library, like `python -m acervo.library.scan`.
//! Usage: inventario --pasta <library> --saida <new reports folder>

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use acervo_core::inventory::{pretty, scan, ScanError};
use acervo_core::messages::{environment_language, tr};

/// `Path.expanduser().resolve()` for a folder that may not exist yet.
fn resolve(value: &str) -> PathBuf {
    let path = match value.strip_prefix("~/") {
        Some(rest) => Path::new(&std::env::var("HOME").unwrap_or_default()).join(rest),
        None => PathBuf::from(value),
    };
    let path = std::path::absolute(&path).unwrap_or(path);
    if let Ok(real) = path.canonicalize() {
        return real;
    }
    match (path.parent(), path.file_name()) {
        (Some(parent), Some(name)) => resolve(&parent.to_string_lossy()).join(name),
        _ => path,
    }
}

fn main() -> ExitCode {
    let language = environment_language();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let value = |flag: &str| {
        args.iter()
            .position(|a| a == flag)
            .and_then(|i| args.get(i + 1))
    };
    let (Some(root), Some(output)) = (value("--pasta"), value("--saida")) else {
        eprintln!("usage: inventario --pasta <library> --saida <new reports folder>");
        return ExitCode::from(2);
    };
    let (root, output) = (resolve(root), resolve(output));
    if !root.is_dir() {
        let message = tr(language, "Music folder does not exist: {root}")
            .replace("{root}", &root.to_string_lossy());
        eprintln!("inventario: error: {message}");
        return ExitCode::from(2);
    }
    match scan(&root, &output, language) {
        Ok(summary) => {
            println!("{}", pretty(&summary));
            println!(
                "{}",
                tr(language, "Reports: {output}").replace("{output}", &output.to_string_lossy())
            );
            let problems = |key: &str| {
                summary[key]
                    .as_array()
                    .is_some_and(|items| !items.is_empty())
            };
            ExitCode::from(
                if problems("erros") || problems("arquivos_com_mudanca_detectada") {
                    2
                } else {
                    0
                },
            )
        }
        Err(error) => {
            let message = match error {
                ScanError::Invalid(message) => message.to_string(),
                ScanError::Io(error) => error.to_string(),
            };
            eprintln!(
                "{}",
                tr(language, "Error: {error}").replace("{error}", &message)
            );
            ExitCode::from(1)
        }
    }
}
