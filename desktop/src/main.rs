//! Acervo's desktop window: starts the local Python server and shows its interface.

use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

struct Server(Mutex<Option<Child>>);

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
}

/// Debug builds run the checkout next to this crate; release builds use the installed package.
fn server_command(port: u16) -> Command {
    let checkout = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    let source = checkout.join("src");
    let use_checkout = cfg!(debug_assertions) && source.join("acervo").is_dir();
    let python = std::env::var_os("ACERVO_PYTHON").map(PathBuf::from).unwrap_or_else(|| {
        let venv = checkout.join(".venv/bin/python");
        if use_checkout && venv.is_file() {
            venv
        } else {
            PathBuf::from("python3")
        }
    });
    let mut command = Command::new(python);
    if use_checkout {
        command.env("PYTHONPATH", &source);
    }
    command.args(["-m", "acervo", "serve", "--port", &port.to_string()]);
    // Anything after the program name goes to the server, such as --folder.
    command.args(std::env::args().skip(1));
    command
}

fn wait_for_server(app: &AppHandle, port: u16) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(60);
    while Instant::now() < deadline {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return Ok(());
        }
        if let Some(child) = app.state::<Server>().0.lock().unwrap().as_mut() {
            if let Ok(Some(status)) = child.try_wait() {
                return Err(format!("The Acervo server stopped ({status})."));
            }
        }
        thread::sleep(Duration::from_millis(150));
    }
    Err("The Acervo server did not start within a minute.".into())
}

fn stop_server(child: &mut Child) {
    // The same signal as Ctrl+C: the server finishes any write to music files before leaving.
    unsafe {
        libc::kill(child.id() as libc::pid_t, libc::SIGINT);
    }
    let deadline = Instant::now() + Duration::from_secs(120);
    while Instant::now() < deadline {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
}

/// The window only shows Acervo itself; any other link opens in the default browser.
fn allowed(url: &Url, port: u16) -> bool {
    let host = url.host_str();
    if url.scheme() == "tauri" || host == Some("tauri.localhost") || (host == Some("127.0.0.1") && url.port() == Some(port)) {
        return true;
    }
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        if let Ok(mut opener) = Command::new("xdg-open").arg(url.as_str()).spawn() {
            thread::spawn(move || opener.wait());
        }
    }
    false
}

fn show_error(window: &WebviewWindow, message: &str) {
    let text = serde_json::to_string(message).unwrap_or_default();
    let _ = window.eval(&format!("window.showError({text})"));
}

fn main() {
    let port = free_port().expect("no free local port");
    let app = tauri::Builder::default()
        .setup(move |app| {
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Acervo")
                .inner_size(1280.0, 860.0)
                .min_inner_size(900.0, 600.0)
                .on_navigation(move |url| allowed(url, port))
                .build()?;
            match server_command(port).spawn() {
                Ok(child) => {
                    app.manage(Server(Mutex::new(Some(child))));
                    let handle = app.handle().clone();
                    thread::spawn(move || {
                        let result = wait_for_server(&handle, port);
                        let Some(window) = handle.get_webview_window("main") else { return };
                        match result {
                            Ok(()) => {
                                let address = Url::parse(&format!("http://127.0.0.1:{port}/")).expect("valid address");
                                let _ = window.navigate(address);
                            }
                            Err(message) => show_error(&window, &message),
                        }
                    });
                }
                Err(error) => {
                    app.manage(Server(Mutex::new(None)));
                    show_error(&window, &format!("Could not start Python: {error}"));
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while starting Acervo");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Server>().0.lock().unwrap().take() {
                stop_server(&mut child);
            }
        }
    });
}
