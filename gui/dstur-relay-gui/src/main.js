const { invoke } = window.__TAURI__.core;

let selectedPort = null;
let relayState = Array(8).fill(false);

function $(id) { return document.getElementById(id); }

async function listPorts() {
  const res = await invoke("relay_list_ports");
  if (!res.ok) throw new Error(res.stderr || res.stdout || "list_ports failed");

  // relay.exe is expected to output JSON when using --json
  // If it returns plain text, you can show res.stdout as-is.
  let ports = [];
  try {
    ports = JSON.parse(res.stdout);
  } catch {
    // Fallback: show raw output
    $("portsRaw").textContent = res.stdout;
    return;
  }

  const sel = $("portSelect");
  sel.innerHTML = `<option value="">(Auto / first detected)</option>`;
  for (const p of ports) {
    // common patterns: p.device, p.name, p.port, etc. Adjust if needed.
    const value = p.device || p.port || p.name || "";
    const label = p.description
      ? `${value} — ${p.description}`
      : value;

    sel.insertAdjacentHTML("beforeend", `<option value="${value}">${label}</option>`);
  }
}

async function refreshStatus() {
  const res = await invoke("relay_status", { port: selectedPort, target: "all" });
  if (!res.ok) throw new Error(res.stderr || res.stdout || "status failed");

  const text = (res.stdout || "").trim();

  // Expected format:
  // relay1=1 relay2=1 relay3=0 ... relay8=0
  for (let i = 1; i <= 8; i++) {
    const m = text.match(new RegExp(`\\brelay${i}=(0|1)\\b`, "i"));
    const on = m ? m[1] === "1" : false;
    relayState[i - 1] = on;
    updateRelayButton(i, on);
  }

  $("statusRaw").textContent = text;
}


function updateRelayButton(relayNum, isOn) {
  const btn = $(`relay${relayNum}`);
  btn.textContent = `Relay ${relayNum}: ${isOn ? "ON" : "OFF"}`;
  btn.dataset.on = isOn ? "1" : "0";
  btn.classList.toggle("on", isOn);
}

async function setRelay(relayNum, state, seconds = null) {
  const payload = { port: selectedPort, relay: relayNum, state, seconds };
  const res = await invoke("relay_set", payload);
  if (!res.ok) throw new Error(res.stderr || res.stdout || `relay ${relayNum} ${state} failed`);
  $("lastCmd").textContent = res.stdout || "(ok)";
}

async function setAll(state, seconds = null) {
  const res = await invoke("relay_all", { port: selectedPort, state, seconds });
  if (!res.ok) throw new Error(res.stderr || res.stdout || `all ${state} failed`);
  $("lastCmd").textContent = res.stdout || "(ok)";
}

function wireUI() {
  $("portSelect").addEventListener("change", (e) => {
    selectedPort = e.target.value || null;
  });

  $("btnPorts").addEventListener("click", async () => {
    await safeRun(listPorts);
  });

  $("btnStatus").addEventListener("click", async () => {
    await safeRun(refreshStatus);
  });

  $("btnAllOn").addEventListener("click", async () => {
    await safeRun(() => setAll("on"));
    await safeRun(refreshStatus);
  });

  $("btnAllOff").addEventListener("click", async () => {
    await safeRun(() => setAll("off"));
    await safeRun(refreshStatus);
  });

  $("btnAllPulse").addEventListener("click", async () => {
    const s = parseFloat($("pulseSeconds").value || "3");
    await safeRun(() => setAll("pulse", s));
    await safeRun(refreshStatus);
  });

  for (let i = 1; i <= 8; i++) {
    $(`relay${i}`).addEventListener("click", async () => {
      const isOn = relayState[i - 1];
      const pulse = $("modePulse").checked;
      if (pulse) {
        const s = parseFloat($("pulseSeconds").value || "1");
        await safeRun(() => setRelay(i, "pulse", s));
      } else {
        await safeRun(() => setRelay(i, isOn ? "off" : "on"));
      }
      await safeRun(refreshStatus);
    });
  }
}

async function safeRun(fn) {
  $("error").textContent = "";
  try {
    await fn();
  } catch (e) {
    $("error").textContent = String(e?.message || e);
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  wireUI();
  await safeRun(listPorts);
  await safeRun(refreshStatus);
});
