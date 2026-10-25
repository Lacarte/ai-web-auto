"""AgentServer against real WebSocket clients: origin pinning, auth, versioning, reconnection races."""

from __future__ import annotations

import asyncio
import json
import unittest
import urllib.request

import websockets
from helpers import free_port

from goal_agent_backend.protocol import PROTOCOL_VERSION
from goal_agent_backend.server import AgentServer

EXT_ORIGIN = "chrome-extension://lfejjmliceaahhcnbdlmafhklbehnpab"
TOKEN = "server-test-token"


class ServerCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = free_port()
        self.server = AgentServer("127.0.0.1", self.port, TOKEN, [EXT_ORIGIN], started_by="manual")
        await self.server.start()

    async def asyncTearDown(self):
        await self.server.stop()

    async def client(self, token=TOKEN, protocol=PROTOCOL_VERSION, origin=EXT_ORIGIN):
        ws = await websockets.connect(f"ws://127.0.0.1:{self.port}", origin=origin)
        await ws.send(json.dumps({"type": "auth", "token": token, "protocol": protocol, "client": "test"}))
        reply = json.loads(await ws.recv())
        return ws, reply

    def http(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


class TestSecurityAndHandshake(ServerCase):
    async def test_health_is_served_on_the_websocket_port(self):
        status, body = await asyncio.to_thread(self.http, "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["app"], "web-goal-agent")
        self.assertEqual(body["protocol"], PROTOCOL_VERSION)
        self.assertNotIn("token", json.dumps(body))

    async def test_web_page_origins_are_rejected_at_the_handshake(self):
        with self.assertRaises(websockets.exceptions.InvalidStatus) as ctx:
            await websockets.connect(f"ws://127.0.0.1:{self.port}", origin="https://evil.example")
        self.assertEqual(ctx.exception.response.status_code, 403)

    async def test_auth_and_version_negotiation(self):
        ws, reply = await self.client()
        self.assertEqual(reply["status"], "accepted")
        self.assertEqual(reply["protocol"], PROTOCOL_VERSION)
        await ws.close()
        ws, reply = await self.client(token="wrong")
        self.assertEqual((reply["status"], reply["reason"]), ("denied", "bad_token"))
        ws, reply = await self.client(protocol=PROTOCOL_VERSION + 1)
        self.assertEqual((reply["status"], reply["reason"]), ("denied", "protocol_mismatch"))

    async def test_shutdown_requires_the_token(self):
        status, _ = await asyncio.to_thread(self.http, "/shutdown", {"X-WGA-Token": "nope"})
        self.assertEqual(status, 403)
        self.assertFalse(self.server._shutdown.is_set())
        status, _ = await asyncio.to_thread(self.http, "/shutdown", {"X-WGA-Token": TOKEN})
        self.assertEqual(status, 200)
        await asyncio.wait_for(self.server.wait_for_shutdown(), 2)


class TestConnectionLifecycle(ServerCase):
    async def test_newest_connection_wins_and_a_stale_close_does_not_clobber_it(self):
        first, _ = await self.client()
        second, _ = await self.client()
        # The server closes the old socket (4001) and keeps the new one.
        with self.assertRaises(websockets.ConnectionClosed):
            for _ in range(5):
                await asyncio.wait_for(first.recv(), 2)
        self.assertEqual(first.close_code, 4001)
        await asyncio.sleep(0.1)
        self.assertTrue(self.server.session.connected, "stale close must not mark the server disconnected")

        async def answer():
            msg = json.loads(await second.recv())
            await second.send(json.dumps({"type": "response", "correlation_id": msg["id"], "status": "success",
                                          "data": {"extracted_data": {"echo": msg["action"]}}}))
        task = asyncio.create_task(answer())
        resp = await self.server.command("locate", {"x": 1}, timeout_s=3)
        await task
        self.assertEqual(resp.data.extracted_data["echo"], "locate")
        await second.close()

    async def test_pending_commands_fail_fast_when_the_client_drops(self):
        ws, _ = await self.client()
        pending = asyncio.create_task(self.server.command("act", {}, timeout_s=10))
        await asyncio.sleep(0.1)
        await ws.close()
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(pending, 3)

    async def test_ping_pong_and_ui_requests_do_not_block_the_receive_loop(self):
        gate = asyncio.Event()

        async def handler(req):
            if req.op == "get_state":
                await gate.wait()  # a slow handler...
            return {"op": req.op}
        self.server.set_ui_handler(handler)
        ws, _ = await self.client()
        await ws.send(json.dumps({"type": "event", "event": "ui_request", "data": {"request_id": "slow", "op": "get_state"}}))
        await ws.send(json.dumps({"type": "ping", "ts": 1}))
        self.assertEqual(json.loads(await asyncio.wait_for(ws.recv(), 2))["type"], "pong")  # ...doesn't stall pings
        await ws.send(json.dumps({"type": "event", "event": "ui_request", "data": {"request_id": "bad", "op": "drop_db"}}))
        bad = json.loads(await asyncio.wait_for(ws.recv(), 2))
        self.assertEqual((bad["event"], bad["data"]["ok"]), ("ui_reply", False))
        gate.set()
        slow = json.loads(await asyncio.wait_for(ws.recv(), 2))
        self.assertEqual((slow["data"]["request_id"], slow["data"]["result"]), ("slow", {"op": "get_state"}))
        await ws.close()


if __name__ == "__main__":
    unittest.main()
