const promptEl = document.getElementById("prompt");
const ratioEl = document.getElementById("ratio");
const autoDownloadEl = document.getElementById("autoDownload");
const generateBtn = document.getElementById("generate");
const statusEl = document.getElementById("status");

// Load saved settings
chrome.storage.local.get(["lastPrompt", "lastRatio", "autoDownload"], (data) => {
  if (data.lastPrompt) promptEl.value = data.lastPrompt;
  if (data.lastRatio) ratioEl.value = data.lastRatio;
  if (data.autoDownload !== undefined) autoDownloadEl.checked = data.autoDownload;
});

generateBtn.addEventListener("click", () => {
  const prompt = promptEl.value.trim();
  if (!prompt) { statusEl.textContent = "Enter a prompt"; return; }

  const options = {
    prompt,
    ratio: ratioEl.value,
    autoDownload: autoDownloadEl.checked,
  };

  // Save settings
  chrome.storage.local.set({
    lastPrompt: prompt,
    lastRatio: options.ratio,
    autoDownload: options.autoDownload,
  });

  chrome.runtime.sendMessage({ type: "generate", options }, (resp) => {
    if (resp?.ok) {
      generateBtn.disabled = true;
      statusEl.className = "status running";
      statusEl.textContent = "Starting...";
    }
  });
});

// Poll state
setInterval(() => {
  chrome.runtime.sendMessage({ type: "getState" }, (resp) => {
    if (!resp?.state) return;
    const s = resp.state;
    statusEl.textContent = s.step || s.status;
    statusEl.className = `status ${s.status}`;
    generateBtn.disabled = s.status === "running";
  });
}, 500);
