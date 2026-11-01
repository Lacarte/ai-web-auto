// Flow Automator — Popup Script

const $ = (s) => document.querySelector(s);
const promptEl = $("#prompt");
const generateBtn = $("#generate");
const statusEl = $("#status");
const statusText = $("#status-text");
const progressBar = $("#progress-bar");
const autoDownloadEl = $("#auto-download");

// Button group selection
document.querySelectorAll(".btn-group").forEach((group) => {
  group.addEventListener("click", (e) => {
    const btn = e.target.closest(".sel");
    if (!btn) return;
    group.querySelectorAll(".sel").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
  });
});

function getSelected(groupId) {
  const active = $(`#${groupId} .sel.active`);
  return active ? active.dataset.value : null;
}

// Generate
generateBtn.addEventListener("click", () => {
  const prompt = promptEl.value.trim();
  if (!prompt) {
    promptEl.focus();
    return;
  }

  const options = {
    prompt,
    mode: getSelected("mode-group") || "Image",
    ratio: getSelected("ratio-group") || "1:1",
    count: parseInt(getSelected("count-group") || "1", 10),
    autoDownload: autoDownloadEl.checked,
  };

  generateBtn.disabled = true;
  statusEl.classList.remove("hidden", "error", "done");
  progressBar.classList.remove("hidden");
  statusText.textContent = "Starting...";

  console.log("[POPUP] Sending options:", JSON.stringify(options));
  chrome.runtime.sendMessage({ type: "generate", options });
});

// Poll state
let pollTimer = null;

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      const resp = await chrome.runtime.sendMessage({ type: "getState" });
      if (!resp?.state) return;
      const s = resp.state;

      statusEl.classList.remove("hidden", "error", "done");
      statusText.textContent = s.step || s.status;

      if (s.status === "done") {
        statusEl.classList.add("done");
        progressBar.classList.add("hidden");
        generateBtn.disabled = false;
      } else if (s.status === "error") {
        statusEl.classList.add("error");
        progressBar.classList.add("hidden");
        generateBtn.disabled = false;
      } else if (s.status === "running") {
        progressBar.classList.remove("hidden");
        generateBtn.disabled = true;
      } else {
        // idle
        generateBtn.disabled = false;
      }
    } catch (_) {}
  }, 500);
}

startPolling();

// Also listen for push updates
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "state") {
    const s = msg.state;
    statusEl.classList.remove("hidden", "error", "done");
    statusText.textContent = s.step || s.status;

    if (s.status === "done") {
      statusEl.classList.add("done");
      progressBar.classList.add("hidden");
      generateBtn.disabled = false;
    } else if (s.status === "error") {
      statusEl.classList.add("error");
      progressBar.classList.add("hidden");
      generateBtn.disabled = false;
    } else if (s.status === "running") {
      progressBar.classList.remove("hidden");
      generateBtn.disabled = true;
    }
  }
});

// Restore last prompt from storage
chrome.storage.local.get(["lastPrompt"], (data) => {
  if (data.lastPrompt) promptEl.value = data.lastPrompt;
});

// Save prompt on change
promptEl.addEventListener("input", () => {
  chrome.storage.local.set({ lastPrompt: promptEl.value });
});
