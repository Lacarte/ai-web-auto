"""AIAgent against a local stand-in for the Messages API, plus the CLI's unknown-command path.

No Anthropic credentials are needed: the real AsyncAnthropic client talks to a stub
HTTP server, so these tests check the request the SDK actually sends and how
current response shapes (leading thinking blocks, refusals, truncation) are read.
"""

import asyncio
import builtins
import http.server
import json
import threading
import unittest
import warnings
from unittest.mock import AsyncMock, patch

warnings.filterwarnings("ignore", category=DeprecationWarning)

import anthropic  # noqa: E402

from ai_web_auto_backend.ai.agent import DEFAULT_MODEL, REFUSAL_FALLBACK_BETA, AIAgent  # noqa: E402
from ai_web_auto_backend.core.models import Response, ResponseData, ResponseStatus  # noqa: E402
from ai_web_auto_backend.core.state import SessionState, Task  # noqa: E402

DECISION = {"reasoning": "open the form", "action": "click", "target": {"method": "css", "value": "#go"},
            "params": {}, "expected_outcome": "form opens", "fallback_action": {}}


def message(content, stop_reason="end_turn", **extra):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": DEFAULT_MODEL,
            "stop_reason": stop_reason, "stop_sequence": None, "content": content,
            "usage": {"input_tokens": 10, "output_tokens": 20}, **extra}


class StubAPI:
    """Records requests to /v1/messages and answers with a queued (status, body)."""

    def __init__(self):
        self.requests = []
        self.replies = []
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, payload = stub.replies.pop(0)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()


class FakeServer:
    def __init__(self):
        self.session = SessionState()
        self.session.browser.url = "https://example.com/"
        self.commands = []

    async def send_command(self, command, timeout=None):
        self.commands.append(command.action)
        elements = [{"tag": "button", "text": "Go", "selector": "#go", "type": "submit"}]
        return Response(correlation_id=command.id, status=ResponseStatus.SUCCESS,
                        data=ResponseData(extracted_data={"elements": elements}))


class AgentDecisionTests(unittest.TestCase):
    def setUp(self):
        self.api = StubAPI()
        self.server = FakeServer()

    def tearDown(self):
        self.api.close()

    def agent(self, **kwargs):
        agent = AIAgent(server=self.server, api_key="test-key", **kwargs)
        agent.client = anthropic.AsyncAnthropic(api_key="test-key", base_url=self.api.url, max_retries=0)
        return agent

    def decide(self, agent):
        async def run():
            try:
                return await agent._decide_next_action(Task(objective="Click Go"), 0)
            finally:
                await agent.client.close()
        return asyncio.run(run())

    def test_request_targets_current_model_with_effort_and_refusal_fallback(self):
        self.api.replies.append((200, message([{"type": "text", "text": json.dumps(DECISION)}])))
        self.assertEqual(self.decide(self.agent()), DECISION)
        req = self.api.requests[0]
        body = req["body"]
        self.assertEqual(body["model"], "claude-sonnet-5-5")
        self.assertEqual(body["max_tokens"], 16000)
        self.assertEqual(body["output_config"], {"effort": "medium"})
        self.assertEqual(body["fallbacks"], "default")
        self.assertIn(REFUSAL_FALLBACK_BETA, req["headers"].get("anthropic-beta", ""))
        for removed in ("temperature", "top_p", "top_k", "thinking"):
            self.assertNotIn(removed, body)
        # The page's interactive elements reach the prompt.
        prompt = body["messages"][0]["content"]
        prompt = prompt if isinstance(prompt, str) else prompt[-1]["text"]
        self.assertIn('selector="#go"', prompt)
        self.assertIn("discover", self.server.commands)

    def test_leading_thinking_and_fallback_blocks_are_skipped(self):
        content = [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "fallback", "from": {"model": "claude-sonnet-5-5"}, "to": {"model": "claude-sonnet-5"}},
            {"type": "text", "text": "```json\n" + json.dumps(DECISION) + "\n```"},
        ]
        self.api.replies.append((200, message(content)))
        self.assertEqual(self.decide(self.agent()), DECISION)

    def test_refusal_is_reported_with_its_category(self):
        self.api.replies.append((200, message([], stop_reason="refusal",
                                              stop_details={"type": "refusal", "category": "cyber", "explanation": None})))
        agent = self.agent()
        self.assertIsNone(self.decide(agent))
        self.assertIn("declined", agent.last_error)
        self.assertIn("cyber", agent.last_error)

    def test_truncated_reply_is_reported(self):
        self.api.replies.append((200, message([{"type": "thinking", "thinking": "", "signature": "s"}], stop_reason="max_tokens")))
        agent = self.agent()
        self.assertIsNone(self.decide(agent))
        self.assertIn("ran out of output tokens", agent.last_error)

    def test_api_errors_are_reported_not_swallowed(self):
        self.api.replies.append((401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
        agent = self.agent()
        self.assertIsNone(self.decide(agent))
        self.assertIn("authentication failed", agent.last_error)

    def test_fallback_can_be_turned_off(self):
        self.api.replies.append((200, message([{"type": "text", "text": json.dumps(DECISION)}])))
        self.decide(self.agent(refusal_fallback=False, effort="low"))
        req = self.api.requests[0]
        self.assertNotIn("fallbacks", req["body"])
        self.assertNotIn(REFUSAL_FALLBACK_BETA, req["headers"].get("anthropic-beta", ""))
        self.assertEqual(req["body"]["output_config"], {"effort": "low"})

    def test_invalid_action_from_the_model_fails_the_step_instead_of_crashing(self):
        agent = self.agent()
        resp = asyncio.run(agent._execute_action({"action": "teleport", "target": {}, "params": {}}))
        self.assertEqual(resp.status, ResponseStatus.FAILURE)
        self.assertIn("teleport", resp.error)


class CliTests(unittest.TestCase):
    def test_unknown_command_is_reported_and_the_prompt_continues(self):
        from ai_web_auto_backend import automation_controller as ctl

        inputs = iter(["bogus command", "quit"])
        printed = []
        with patch.object(ctl.BrowserAgent, "connect", AsyncMock()), \
             patch.object(ctl.BrowserAgent, "stop", AsyncMock()), \
             patch.object(builtins, "input", lambda *_: next(inputs)), \
             patch.object(builtins, "print", lambda *a, **k: printed.append(" ".join(map(str, a)))):
            asyncio.run(ctl.main())
        self.assertTrue(any("Unknown command: 'bogus'" in line for line in printed), printed[-3:])


if __name__ == "__main__":
    unittest.main()
