"""
ChatGPT API Bridge — REST API Server

Exposes ChatGPT as a local REST API:
  POST http://localhost:8767/prompt  {"prompt": "your question"}
  → {"success": true, "response": "ChatGPT's answer", "prompt": "your question"}

Communicates with the Chrome extension via WebSocket on port 8766.

Usage:
  pip install fastapi uvicorn websockets
  python api_server.py
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import uvicorn
import websockets
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ── State ────────────────────────────────────────────────────────────────────

extension_ws = None
pending_requests: dict[str, asyncio.Future] = {}


# ── WebSocket server (extension connects here) ──────────────────────────────

async def ws_handler(websocket):
    global extension_ws
    extension_ws = websocket
    print("[WS] Extension connected")

    try:
        async for raw in websocket:
            msg = json.loads(raw)

            if msg.get("type") == "hello":
                print(f"[WS] Extension identified: {msg.get('from')}")
                continue

            if msg.get("type") == "response":
                req_id = msg.get("id")
                fut = pending_requests.pop(req_id, None)
                if fut and not fut.done():
                    fut.set_result(msg)
                continue

    except websockets.ConnectionClosed:
        print("[WS] Extension disconnected")
    finally:
        extension_ws = None


async def start_ws_server():
    server = await websockets.serve(ws_handler, "localhost", 8766, ping_interval=30)
    print("[WS] WebSocket server listening on ws://localhost:8766")
    return server


# ── FastAPI app ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    server = await start_ws_server()
    yield
    server.close()

app = FastAPI(title="ChatGPT API Bridge", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class PromptRequest(BaseModel):
    prompt: str
    append_suffix: bool = True
    timeout: int = 120


class PromptResponse(BaseModel):
    success: bool
    response: str = ""
    prompt: str = ""
    error: str = ""


@app.post("/prompt", response_model=PromptResponse)
async def send_prompt(req: PromptRequest):
    if not extension_ws:
        return PromptResponse(success=False, error="Extension not connected. Load the ChatGPT API Bridge extension and open chatgpt.com")

    req_id = str(uuid.uuid4())
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    pending_requests[req_id] = fut

    # Send to extension
    await extension_ws.send(json.dumps({
        "type": "prompt",
        "id": req_id,
        "prompt": req.prompt,
        "options": {
            "appendSuffix": req.append_suffix,
            "timeoutMs": req.timeout * 1000,
        },
    }))

    # Wait for response
    try:
        result = await asyncio.wait_for(fut, timeout=req.timeout + 10)
        return PromptResponse(
            success=result.get("success", False),
            response=result.get("response", ""),
            prompt=result.get("prompt", req.prompt),
            error=result.get("error", ""),
        )
    except asyncio.TimeoutError:
        pending_requests.pop(req_id, None)
        return PromptResponse(success=False, error="Timeout waiting for ChatGPT response")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "extension_connected": extension_ws is not None,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8767)
