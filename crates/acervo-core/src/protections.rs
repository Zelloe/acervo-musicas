//! Protected fields: user choices that Acervo may neither propose nor write.
//! Only the reading side of `acervo.library.protections`; the file format is the same.

use std::collections::HashSet;
use std::fs;
use std::path::{Component, Path, PathBuf};

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::messages::{tr, Language};

pub const FIELDS: [&str; 8] = [
    "titulo",
    "artista",
    "artista_album",
    "album",
    "genero",
    "ano",
    "faixa",
    "disco",
];
pub const SCHEMA: u64 = 1;

pub type Policy = Value;

/// One file per canonical library, so collections sharing the same data never mix.
pub fn policy_path(storage: &Path, root: &Path) -> PathBuf {
    let root = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    let digest = format!("{:x}", Sha256::digest(root.to_string_lossy().as_bytes()));
    let storage = storage
        .canonicalize()
        .unwrap_or_else(|_| storage.to_path_buf());
    storage
        .join(".biblioteca")
        .join("protecoes")
        .join(format!("{}.json", &digest[..16]))
}

pub fn empty(root: &Path) -> Policy {
    let root = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    json!({"schema": SCHEMA, "biblioteca": root.to_string_lossy(), "faixas": {}})
}

/// Fails closed: only a confirmed missing file counts as an empty policy.
pub fn load(storage: &Path, root: &Path, language: Language) -> Result<Policy, String> {
    let path = policy_path(storage, root);
    let message =
        |text: &'static str| tr(language, text).replace("{path}", &path.to_string_lossy());
    let info = match path.symlink_metadata() {
        Ok(info) => info,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(empty(root)),
        Err(_) => {
            return Err(message(
                "Could not read the protected fields; nothing will be written: {path}",
            ))
        }
    };
    if !info.file_type().is_file() {
        return Err(message(
            "The protected fields file must be a regular file, not a link: {path}",
        ));
    }
    let policy: Value = fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .ok_or_else(|| {
            message("Unreadable protected fields file; nothing will be written: {path}")
        })?;
    let incompatible =
        || message("Incompatible protected fields file; nothing will be written: {path}");
    if policy["schema"] != json!(SCHEMA) || !policy["faixas"].is_object() {
        return Err(incompatible());
    }
    let canonical = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    if policy["biblioteca"] != json!(canonical.to_string_lossy()) {
        return Err(message(
            "The protected fields file belongs to another library: {path}",
        ));
    }
    for (relative, entry) in policy["faixas"].as_object().unwrap() {
        let valid = entry["campos"].as_array().is_some_and(|fields| {
            fields
                .iter()
                .all(|f| f.as_str().is_some_and(|f| FIELDS.contains(&f)))
        });
        let relative = Path::new(relative);
        if !valid
            || relative.is_absolute()
            || relative.components().any(|c| c == Component::ParentDir)
        {
            return Err(incompatible());
        }
    }
    Ok(policy)
}

/// Protected fields of one track of the library.
pub fn protected(policy: &Policy, root: &Path, file: &Path) -> Result<HashSet<String>, String> {
    let canonical = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    let relative = file.strip_prefix(&canonical).map_err(|_| {
        format!(
            "'{}' is not in the subpath of '{}'",
            file.display(),
            canonical.display()
        )
    })?;
    let key = relative.to_string_lossy();
    Ok(policy["faixas"][key.as_ref()]["campos"]
        .as_array()
        .map(|fields| {
            fields
                .iter()
                .filter_map(|f| f.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default())
}
