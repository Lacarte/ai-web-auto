# ChatGPT API Bridge

Chrome extension + local REST API server that turns your ChatGPT browser session into a programmable API.

```
Your app  -->  POST /prompt  -->  Python server (:8767)
                                       | WebSocket (:8766)
                                  Chrome Extension
                                       | CDP automation
                                  ChatGPT tab (chatgpt.com)
```

## Quick Start

### 1. Install the extension

1. Open `chrome://extensions` in Chrome
2. Enable **Developer mode** (top right toggle)
3. Click **Load unpacked** and select `chatgpt-api-extension/`
4. Open [https://chatgpt.com/](https://chatgpt.com/) and make sure you're logged in

### 2. Set up the API server

```bash
cd chatgpt-api-backend
setup.bat
```

This creates a Python venv and installs `fastapi`, `uvicorn`, `websockets`.

### 3. Start the server

```bash
cd chatgpt-api-backend
run.bat
```

The server starts on:
- **REST API**: `http://localhost:8767`
- **WebSocket** (extension link): `ws://localhost:8766`

### 4. Send prompts

**cURL:**
```bash
curl -X POST http://localhost:8767/prompt ^
  -H "Content-Type: application/json" ^
  -d "{\"prompt\": \"What is 2+2? Answer with just the number.\"}"
```

**Response:**
```json
{
  "success": true,
  "response": "4",
  "prompt": "What is 2+2? Answer with just the number.",
  "error": ""
}
```

**VS Code REST Client:** open `chatgpt-api-backend/test.rest` and click "Send Request" above any block.

**Extension popup:** click the extension icon in Chrome toolbar, type a prompt, click Send.

## API Reference

### `POST /prompt`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `prompt` | string | (required) | The text to send to ChatGPT |
| `append_suffix` | bool | `true` | Appends " — Respond directly and concisely, no markdown, no extra commentary." to the prompt |
| `timeout` | int | `120` | Max seconds to wait for ChatGPT's response |

**Response:**
```json
{
  "success": true,
  "response": "ChatGPT's answer text",
  "prompt": "original prompt",
  "error": ""
}
```

### `GET /health`

```json
{
  "status": "ok",
  "extension_connected": true
}
```

## How It Works

1. The Python server receives your HTTP request
2. It forwards the prompt to the Chrome extension via WebSocket
3. The extension automates the ChatGPT tab using CDP (Chrome DevTools Protocol):
   - Clicks the input (`#prompt-textarea`, a ProseMirror contentEditable div)
   - Types the prompt char-by-char via `Input.insertText`
   - Clicks the send button (`[data-testid="send-button"]`)
   - Polls for response completion (watches `[data-message-author-role="assistant"]` and `[data-testid="stop-button"]`)
   - Extracts the response text via `innerText`
4. The response is sent back through WebSocket to the Python server
5. The server returns it as JSON in the HTTP response

## File Structure

```
chatgpt-api/
├── README.md
├── chatgpt-api-extension/     # Load this in Chrome
│   ├── manifest.json          # Chrome extension manifest (MV3)
│   ├── js/
│   │   ├── background.js      # CDP automation + WebSocket client
│   │   └── popup.js           # Extension popup logic
│   ├── html/
│   │   └── popup.html         # Extension popup UI
│   └── css/
│       └── popup.css          # Popup styling
└── chatgpt-api-backend/
    ├── api_server.py          # FastAPI REST server + WebSocket relay
    ├── setup.bat              # One-time: create venv + install deps
    ├── run.bat                # Start the API server
    └── test.rest              # REST Client test file (VS Code)
```

## Tips

- **Keep ChatGPT open** in at least one Chrome tab. The extension finds or opens the tab automatically.
- **One request at a time.** The extension queues nothing — wait for one response before sending the next.
- **Raw mode:** set `"append_suffix": false` to send prompts without the "respond concisely" instruction.
- **JSON responses:** ask ChatGPT to "return ONLY valid JSON" in your prompt for structured output.
- **Newlines in prompts** are automatically converted to spaces (ProseMirror treats `\n` as Enter/submit).

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `extension_connected: false` | Reload extension in `chrome://extensions`, check console for errors |
| No response / timeout | Make sure ChatGPT tab is open and logged in, no CAPTCHA blocking |
| Prompt typed but not sent | Reload extension — send button selector may have changed |
| Partial response | Increase `timeout` in the request (default 120s) |
| Port 8766/8767 in use | Kill the old process or change ports in `api_server.py` and `background.js` |
