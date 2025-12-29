use serde::Serialize;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};
use std::sync::OnceLock;

static RELAY_PATH: OnceLock<PathBuf> = OnceLock::new();

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[derive(Serialize)]
struct CmdResult {
  ok: bool,
  code: Option<i32>,
  stdout: String,
  stderr: String,
}

fn first_existing(candidates: &[PathBuf]) -> Option<PathBuf> {
  candidates.iter().find(|p| p.exists()).cloned()
}

fn relay_exe_path(app: &AppHandle) -> Result<PathBuf, String> {
  if let Some(p) = RELAY_PATH.get() {
    return Ok(p.clone());
  }

  let mut candidates: Vec<PathBuf> = Vec::new();

  if let Ok(exe_dir) = app.path().executable_dir() {
    candidates.push(exe_dir.join("relay.exe"));
  }

  if let Ok(exe) = std::env::current_exe() {
    if let Some(dir) = exe.parent() {
      candidates.push(dir.join("relay.exe"));
    }
  }

  let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
  candidates.push(manifest_dir.join("bin").join("relay.exe"));
  candidates.push(manifest_dir.join("..").join("relay.exe"));
  candidates.push(manifest_dir.join("..").join("..").join("relay.exe"));

  if let Some(found) = first_existing(&candidates) {
    let _ = RELAY_PATH.set(found.clone());
    return Ok(found);
  }

  let checked = candidates
    .iter()
    .map(|p| format!("  - {}", p.display()))
    .collect::<Vec<_>>()
    .join("\n");

  Err(format!(
    "relay.exe not found. Checked:\n{checked}\n\nFix: place relay.exe in src-tauri/bin/relay.exe (recommended) and set bundle.externalBin to [\"bin/relay.exe\"], or place relay.exe next to the running executable."
  ))
}


fn run_relay(app: &AppHandle, args: &[String]) -> Result<CmdResult, String> {
  let exe = relay_exe_path(app)?;

  let mut cmd = std::process::Command::new(&exe);
  cmd.args(args);

  // Prevent console window flashing on Windows
  #[cfg(windows)]
  {
    cmd.creation_flags(CREATE_NO_WINDOW);
  }

  let output = cmd
    .output()
    .map_err(|e| format!("Failed to run {}: {e}", exe.display()))?;

  Ok(CmdResult {
    ok: output.status.success(),
    code: output.status.code(),
    stdout: String::from_utf8_lossy(&output.stdout).to_string(),
    stderr: String::from_utf8_lossy(&output.stderr).to_string(),
  })
}

#[tauri::command]
fn relay_list_ports(app: AppHandle) -> Result<CmdResult, String> {
  let args = vec!["list-ports".to_string(), "--json".to_string()];
  run_relay(&app, &args)
}

#[tauri::command]
fn relay_status(app: AppHandle, port: Option<String>, target: String) -> Result<CmdResult, String> {
  let mut args: Vec<String> = Vec::new();
  if let Some(p) = port {
    args.push("--port".into());
    args.push(p);
  }
  args.push("status".into());
  args.push(target);
  run_relay(&app, &args)
}

#[tauri::command]
fn relay_set(
  app: AppHandle,
  port: Option<String>,
  relay: u8,
  state: String,
  seconds: Option<f32>,
) -> Result<CmdResult, String> {
  if relay < 1 || relay > 8 {
    return Err("Relay number must be 1..8".into());
  }

  let mut args: Vec<String> = Vec::new();
  if let Some(p) = port {
    args.push("--port".into());
    args.push(p);
  }
  args.push("relay".into());
  args.push(relay.to_string());
  args.push(state);

  if let Some(s) = seconds {
    args.push("--seconds".into());
    args.push(s.to_string());
  }

  run_relay(&app, &args)
}

#[tauri::command]
fn relay_all(
  app: AppHandle,
  port: Option<String>,
  state: String,
  seconds: Option<f32>,
) -> Result<CmdResult, String> {
  let mut args: Vec<String> = Vec::new();
  if let Some(p) = port {
    args.push("--port".into());
    args.push(p);
  }
  args.push("all".into());
  args.push(state);

  if let Some(s) = seconds {
    args.push("--seconds".into());
    args.push(s.to_string());
  }

  run_relay(&app, &args)
}

pub fn run() {
  tauri::Builder::default()
    .invoke_handler(tauri::generate_handler![
      relay_list_ports,
      relay_status,
      relay_set,
      relay_all
    ])
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
