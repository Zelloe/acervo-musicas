//! Prints the inventory row of each file given, as JSON lines, for comparison with the Python version.

fn main() {
    for path in std::env::args().skip(1) {
        let row = acervo_core::scan::read_track(std::path::Path::new(&path))
            .unwrap_or_else(|error| serde_json::json!({ "arquivo": path, "erro": error }));
        println!("{row}");
    }
}
