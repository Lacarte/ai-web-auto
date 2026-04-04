# ChatGPT UI-to-API Bridge

**Date:** Pre-2026-04
**Status:** Completed
**Goal:** Turn the ChatGPT web UI into a local REST API — send prompts programmatically via HTTP, get responses as JSON.

## What it does
A Chrome extension + Python backend that exposes ChatGPT as a local REST API:
1. Extension connects to a local WebSocket server (port 8766)
2. Python backend runs a FastAPI REST server (port 8767)
3. Send `POST /prompt {"prompt": "your question"}` to the REST API
4. Backend forwards to extension via WebSocket
5. Extension types the prompt into ChatGPT's UI via CDP, waits for response
6. Response is sent back through WebSocket to REST API
7. Returns `{"success": true, "response": "ChatGPT's answer"}`

## Website automated
ChatGPT — https://chatgpt.com/

## Architecture
Two-component system:
- **Chrome extension** (chatgpt-ui-to-api-extension/)
  - Service worker connects to WebSocket server
  - Receives prompts, types into ChatGPT via CDP
  - Polls for response completion, extracts answer
  - Sends response back via WebSocket
- **Python backend** (chatgpt-ui-to-api-backend/)
  - FastAPI REST server on port 8767
  - WebSocket server on port 8766 (extension connects here)
  - Bridges HTTP requests to WebSocket messages

## Key technical patterns

### REST-to-WebSocket Bridge
HTTP request comes in -> assigned a UUID -> forwarded via WebSocket to extension -> extension automates ChatGPT -> response sent back via WebSocket -> HTTP response returned. Uses `asyncio.Future` for request/response correlation.

### CDP Automation on ChatGPT
Types prompts into ChatGPT's contentEditable input using CDP `Input.insertText`. Polls for response completion by watching for the stop button to disappear and response text to stabilize.

## Usage
```
# Terminal 1: Start the backend
cd chatgpt-ui-to-api-backend
run.bat

# Terminal 2: Send prompts
curl -X POST http://localhost:8767/prompt -H "Content-Type: application/json" -d "{\"prompt\": \"What is 2+2?\"}"
```

## Files
```
chatgpt-ui-to-api/
  chatgpt-ui-to-api-extension/
    manifest.json
    js/popup.js
    js/background.js     # WebSocket client + CDP automation
    html/popup.html
  chatgpt-ui-to-api-backend/
    api_server.py         # FastAPI + WebSocket server
    run.bat
    setup.bat
```
