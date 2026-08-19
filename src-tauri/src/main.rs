#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use tauri::api::process::Command;
use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let window = app.get_window("main").unwrap();
            
            // Spawn the sidecar (Streamlit backend)
            let (mut rx, mut _child) = Command::new_sidecar("medical_deidentifier_backend")
                .expect("failed to create sidecar command")
                .spawn()
                .expect("Failed to spawn sidecar");

            // Monitor stdout to know when Streamlit is ready
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let tauri::api::process::CommandEvent::Stdout(line) = event {
                        println!("Backend: {}", line);
                        // Streamlit is ready when it prints the Network URL
                        if line.contains("Network URL:") || line.contains("Local URL:") {
                            println!("Streamlit is ready, redirecting window...");
                            window.eval("window.location.replace('http://localhost:8501')").unwrap();
                        }
                    } else if let tauri::api::process::CommandEvent::Stderr(line) = event {
                        eprintln!("Backend Error: {}", line);
                    }
                }
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
