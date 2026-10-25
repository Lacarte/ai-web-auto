"""Core WebSocketServer: an order bound to a browser tab never reaches another browser or tab."""

import asyncio
import json
import sys
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore", category=DeprecationWarning)
from ai_web_auto_backend.core.models import Command  # noqa: E402
from ai_web_auto_backend.core.server import WebSocketServer  # noqa: E402


class FakeExtension:
    """One browser's extension on the socket: authenticates, records what it receives."""

    def __init__(self, capabilities=("tab_id",)):
        self.auth = json.dumps({"type": "auth", "token": "local-dev-token", "capabilities": list(capabilities)})
        self.received = []
        self.remote_address = ("127.0.0.1", 0)
        self._closed = asyncio.Event()

    async def recv(self):
        return self.auth

    async def send(self, raw):
        self.received.append(json.loads(raw))

    async def close(self):
        self._closed.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._closed.wait()
        raise StopAsyncIteration

    @property
    def commands(self):
        return [m for m in self.received if m.get("type") != "auth_response"]


async def connect(server, ext):
    task = asyncio.create_task(server._handle_connection(ext))
    for _ in range(50):
        await asyncio.sleep(0)
        if ext in server._connections:
            return task
    raise AssertionError("extension did not authenticate")


async def disconnect(ext, task):
    await ext.close()
    await task


def script():
    return Command(action="execute_script", params={"script": "1"})


class TestBinding(unittest.TestCase):
    def test_bound_commands_name_the_tab(self):
        async def scenario():
            s, a = WebSocketServer(), FakeExtension()
            ta = await connect(s, a)
            s.bind(42)
            await s.send_command(script(), timeout=0.05)
            await disconnect(a, ta)
            return a.commands
        sent = asyncio.run(scenario())
        self.assertEqual(sent[-1]["params"]["tab_id"], 42)

    def test_second_browser_never_receives_a_bound_order(self):
        async def scenario():
            s, a, b = WebSocketServer(), FakeExtension(), FakeExtension()
            ta = await connect(s, a)
            s.bind(42)
            tb = await connect(s, b)                 # e.g. the user's everyday Chrome reconnects
            resp = await s.send_command(script(), timeout=0.05)
            await disconnect(b, tb)
            await disconnect(a, ta)
            return resp, a.commands, b.commands
        resp, to_a, to_b = asyncio.run(scenario())
        self.assertEqual(resp.status, "failure")
        self.assertIn("connection changed", resp.error)
        self.assertEqual((to_a, to_b), ([], []))

    def test_unbound_commands_follow_the_newest_browser(self):
        async def scenario():
            s, a, b = WebSocketServer(), FakeExtension(), FakeExtension()
            ta, tb = await connect(s, a), await connect(s, b)
            await s.send_command(script(), timeout=0.05)
            await disconnect(b, tb)
            await disconnect(a, ta)
            return a.commands, b.commands
        to_a, to_b = asyncio.run(scenario())
        self.assertEqual((len(to_a), len(to_b)), (0, 1))
        self.assertNotIn("tab_id", to_b[0]["params"])

    def test_replaced_connection_closing_late_keeps_the_live_one(self):
        async def scenario():
            s, a, b = WebSocketServer(), FakeExtension(), FakeExtension()
            ta, tb = await connect(s, a), await connect(s, b)
            await disconnect(a, ta)
            live = (s._ws is b, s.session.connected)
            await disconnect(b, tb)
            return live, s.session.connected
        live, after = asyncio.run(scenario())
        self.assertEqual(live, (True, True))
        self.assertFalse(after)

    def test_order_bound_to_the_newest_survives_an_old_socket_closing(self):
        async def scenario():
            s, a, b = WebSocketServer(), FakeExtension(), FakeExtension()
            ta, tb = await connect(s, a), await connect(s, b)
            s.bind(9)                                 # bound on b
            await disconnect(a, ta)                   # the replaced socket closes late
            await s.send_command(script(), timeout=0.05)
            await disconnect(b, tb)
            return b.commands
        to_b = asyncio.run(scenario())
        self.assertEqual(len(to_b), 1)
        self.assertEqual(to_b[0]["params"]["tab_id"], 9)

    def test_newest_leaving_falls_back_and_stops_its_bound_order(self):
        async def scenario():
            s, a, b = WebSocketServer(), FakeExtension(capabilities=()), FakeExtension()
            ta, tb = await connect(s, a), await connect(s, b)
            s.bind(7)                                 # bound on b
            caps_b = set(s.capabilities)
            await disconnect(b, tb)
            resp = await s.send_command(script(), timeout=0.05)
            state = (s._ws is a, s.session.connected, set(s.capabilities))
            await disconnect(a, ta)
            return caps_b, resp, state, a.commands
        caps_b, resp, state, to_a = asyncio.run(scenario())
        self.assertEqual(caps_b, {"tab_id"})
        self.assertEqual(resp.status, "failure")
        self.assertEqual(state, (True, True, set()))
        self.assertEqual(to_a, [])


if __name__ == "__main__":
    unittest.main()
