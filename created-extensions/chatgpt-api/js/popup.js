const promptEl = document.getElementById("prompt");
const sendBtn = document.getElementById("send");
const statusEl = document.getElementById("status");
const responseEl = document.getElementById("response");
const noSuffixEl = document.getElementById("noSuffix");

sendBtn.addEventListener("click", () => {
  const prompt = promptEl.value.trim();
  if (!prompt) { statusEl.textContent = "Enter a prompt"; return; }

  sendBtn.disabled = true;
  statusEl.className = "status running";
  statusEl.textContent = "Sending...";
  responseEl.classList.add("hidden");

  chrome.runtime.sendMessage(
    { type: "prompt", prompt, options: { appendSuffix: !noSuffixEl.checked } },
    (result) => {
      sendBtn.disabled = false;
      if (result?.success) {
        statusEl.className = "status done";
        statusEl.textContent = "Done";
        responseEl.textContent = result.response;
        responseEl.classList.remove("hidden");
      } else {
        statusEl.className = "status error";
        statusEl.textContent = result?.error || "Failed";
      }
    }
  );
});

// Poll state
setInterval(() => {
  chrome.runtime.sendMessage({ type: "getState" }, (resp) => {
    if (!resp?.state) return;
    const s = resp.state;
    if (s.status === "running") {
      statusEl.textContent = s.step || "Working...";
      statusEl.className = "status running";
      sendBtn.disabled = true;
    }
  });
}, 1000);
