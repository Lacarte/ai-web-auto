"""Engine behaviour against a scripted fake browser: verification, recovery, safety, control."""

from __future__ import annotations

import asyncio
import unittest

from helpers import ORIGIN, Disconnected, FakeBrowser, fact, goal, make_config, tempdir

from goal_agent_backend.engine import Engine, RunState
from goal_agent_backend.protocol import ConfirmArgs, RunRef, StartRunArgs

CLICK = {"role": "button", "name": "Go"}


class EngineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempdir()
        self.browser = FakeBrowser()
        self.events: list[dict] = []

    async def push(self, event, data):
        self.events.append({"event": event, **data})
        return True

    def engine(self, goals, **cfg) -> Engine:
        config = make_config(self.tmp, goals, **cfg)
        return Engine(config, self.browser, self.push, planner_factory=None, is_connected=lambda: True)

    async def run_goal(self, eng: Engine, goal_id="g", inputs=None, until=None, timeout=15):
        snap = await eng.start_run(StartRunArgs(tab_id=7, goal_id=goal_id, inputs=inputs or {},
                                                granted_origins=[ORIGIN]))
        run = eng.run
        if until:
            await until(run)
        await asyncio.wait_for(run.task, timeout)
        return eng.last_run, snap

    def kinds(self, kind):
        return [e for e in self.events if e.get("kind") == kind]


class TestVerification(EngineCase):
    async def test_success_requires_evidence_for_every_step_and_the_goal(self):
        g = goal([
            {"id": "open", "action": "click", "target": CLICK, "expect": [fact("dialog")]},
            {"id": "name", "action": "type", "target": {"label": "Name"}, "value": "Ada"},
        ], [fact("saved")])

        def on_act(action, label):
            if action == "click":
                self.browser.facts["dialog"] = True
            if action == "type":
                self.browser.facts["value:Ada"] = True
                self.browser.facts["saved"] = True
        self.browser.on_act = on_act
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.state, RunState.SUCCEEDED, run.error)
        self.assertEqual([a for a, _ in self.browser.acts], ["click", "type"])
        self.assertEqual([s["status"] for s in run.steps], ["verified", "verified"])
        self.assertIn(("release", "succeeded"), self.browser.calls)

    async def test_goal_fails_when_success_criteria_unmet_even_if_steps_verified(self):
        g = goal([{"id": "open", "action": "click", "target": CLICK, "expect": [fact("dialog")]}], [fact("saved")])
        self.browser.on_act = lambda a, l: self.browser.facts.update(dialog=True)
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual(run.error["code"], "goal_not_reached")

    async def test_already_satisfied_goal_does_nothing(self):
        g = goal([{"id": "open", "action": "click", "target": CLICK, "expect": [fact("dialog")]}], [fact("saved")])
        self.browser.facts["saved"] = True
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.state, RunState.SUCCEEDED)
        self.assertEqual(self.browser.acts, [])

    async def test_step_already_in_expected_state_is_not_repeated(self):
        g = goal([{"id": "open", "action": "click", "target": CLICK, "expect": [fact("dialog")]}], [fact("saved")],
                 check_success_first=False)
        self.browser.facts.update(dialog=True, saved=True)
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.state, RunState.SUCCEEDED)
        self.assertEqual(self.browser.acts, [])
        self.assertEqual(run.steps[0]["note"], "already in the expected state")


class TestRecovery(EngineCase):
    async def test_ambiguous_target_is_refused_without_acting(self):
        g = goal([{"id": "save", "action": "click", "target": {"role": "button", "name": "Save"}, "expect": [fact("x")]}],
                 [fact("x")])
        self.browser.locate_script = [{"ok": False, "code": "ambiguous", "count": 2, "candidates": [{}, {}]}]
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual(run.error["code"], "ambiguous")
        self.assertEqual(self.browser.acts, [])

    async def test_non_repeatable_click_is_dispatched_once(self):
        g = goal([{"id": "submit", "action": "click", "target": CLICK, "expect": [fact("done")]}], [fact("done")])
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual(run.error["code"], "not_verified")
        self.assertEqual(len(self.browser.acts), 1)

    async def test_repeatable_step_is_retried_within_bounds(self):
        g = goal([{"id": "t", "action": "type", "target": {"label": "Name"}, "value": "Ada", "retry": {"attempts": 3}}],
                 [fact("x")])
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual(len(self.browser.acts), 3)

    async def test_transient_locate_failures_are_waited_out(self):
        g = goal([{"id": "go", "action": "click", "target": CLICK, "expect": [fact("ok")]}], [fact("ok")])
        self.browser.locate_script = [{"ok": False, "code": "disabled", "message": "disabled"},
                                      {"ok": False, "code": "obstructed", "obstructed_by": "toast"},
                                      {"ok": True}]
        self.browser.on_act = lambda a, l: self.browser.facts.update(ok=True)
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.state, RunState.SUCCEEDED, run.error)
        self.assertEqual(len(self.browser.acts), 1)

    async def test_page_reported_error_stops_without_repeating(self):
        g = goal([{"id": "save", "action": "click", "target": CLICK, "expect": [fact("closed")]}], [fact("closed")])
        self.browser.on_act = lambda a, l: self.browser.alerts.append("Email: Enter a valid email address")
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.error["code"], "page_reported_error")
        self.assertIn("valid email", run.error["message"])
        self.assertEqual(len(self.browser.acts), 1)

    async def test_interrupt_handler_clears_an_obstruction(self):
        g = goal([{"id": "go", "action": "click", "target": CLICK, "expect": [fact("ok")]}], [fact("ok")],
                 interrupts=[{"id": "cookie", "when": [fact("banner")],
                              "do": {"id": "dismiss", "action": "click", "target": {"role": "button", "name": "Reject"},
                                     "expect": [{"text_absent": {"text": "banner"}}]}}])
        self.browser.facts["banner"] = True
        self.browser.locate_script = [{"ok": True}]  # interrupt's own target

        def on_act(action, label):
            if self.browser.facts.get("banner"):
                self.browser.facts["banner"] = False
            else:
                self.browser.facts["ok"] = True
        self.browser.on_act = on_act
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.state, RunState.SUCCEEDED, run.error)
        self.assertEqual(len(self.browser.acts), 2)
        self.assertTrue(any("cookie" in e.get("text", "") for e in self.kinds("recover")))

    async def test_leaving_the_authorized_site_fails_the_run(self):
        g = goal([{"id": "go", "action": "click", "target": CLICK, "expect": [fact("ok")]}], [fact("ok")])

        def on_act(action, label):
            self.browser.url = "https://evil.example/phish"
            self.browser.facts["ok"] = True
        self.browser.on_act = on_act
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.error["code"], "left_authorized_site")


class TestInterruptedExecution(EngineCase):
    async def test_disconnect_mid_action_with_effect_present_is_recovered_without_repeating(self):
        g = goal([{"id": "save", "action": "click", "target": CLICK, "expect": [fact("saved")]}], [fact("saved")])
        err = Disconnected()
        err._apply_effect = True  # the click landed before the connection dropped
        self.browser.act_errors = [err]
        self.browser.on_act = lambda a, l: self.browser.facts.update(saved=True)
        run, _ = await self.run_goal(self.engine([g]))
        self.assertEqual(run.state, RunState.SUCCEEDED, run.error)
        self.assertEqual(len(self.browser.acts), 1)
        self.assertGreaterEqual(sum(1 for c in self.browser.calls if c[0] == "bind"), 2)  # re-bound after reconnect

    async def test_disconnect_mid_action_without_effect_asks_the_human_and_never_repeats(self):
        g = goal([{"id": "save", "action": "click", "target": CLICK, "expect": [fact("saved")]}], [fact("saved")])
        self.browser.act_errors = [Disconnected()]
        eng = self.engine([g])

        async def resume_when_asked(run):
            for _ in range(200):
                if run.state == RunState.WAITING_HUMAN:
                    await eng.resume(RunRef(run_id=run.id))
                    return
                await asyncio.sleep(0.02)
            self.fail("never asked the human")
        run, _ = await self.run_goal(eng, until=resume_when_asked)
        self.assertEqual(run.error["code"], "unverified_after_interruption")
        self.assertEqual(len(self.browser.acts), 0)  # the lost click is never re-sent


class TestControlAndSafety(EngineCase):
    def slow_goal(self):
        self.browser.locate_script = [{"ok": False, "code": "disabled", "message": "disabled"}] * 30 + [{"ok": True}]
        self.browser.on_act = lambda a, l: self.browser.facts.update(ok=True)
        return goal([{"id": "go", "action": "click", "target": CLICK, "expect": [fact("ok")], "timeout_s": 10}], [fact("ok")])

    async def test_pause_holds_actions_until_resume(self):
        eng = self.engine([self.slow_goal()])

        async def pause_then_resume(run):
            await asyncio.sleep(0.1)
            await eng.pause(RunRef(run_id=run.id))
            await asyncio.sleep(0.05)
            self.assertEqual(run.state, RunState.PAUSED)
            self.browser.locate_script = [{"ok": True}]
            await asyncio.sleep(0.4)
            self.assertEqual(self.browser.acts, [], "acted while paused")
            await eng.resume(RunRef(run_id=run.id))
        run, _ = await self.run_goal(eng, until=pause_then_resume)
        self.assertEqual(run.state, RunState.SUCCEEDED)
        self.assertEqual(len(self.browser.acts), 1)

    async def test_stop_ends_the_run_without_acting(self):
        eng = self.engine([self.slow_goal()])

        async def stop(run):
            await asyncio.sleep(0.1)
            await eng.stop(RunRef(run_id=run.id))
        run, _ = await self.run_goal(eng, until=stop)
        self.assertEqual(run.state, RunState.STOPPED)
        self.assertEqual(self.browser.acts, [])

    async def confirm_flow(self, approve: bool):
        g = goal([{"id": "del", "action": "click", "target": {"role": "button", "name": "Delete"}, "expect": [fact("gone")]}],
                 [fact("gone")])
        self.browser.target_context = {"role": "button", "name": "Delete", "dialog_name": "Delete Ada?", "dialog_role": "alertdialog"}
        self.browser.on_act = lambda a, l: self.browser.facts.update(gone=True)
        eng = self.engine([g])

        async def answer(run):
            for _ in range(200):
                if run.pending_confirmation:
                    self.assertEqual(self.browser.acts, [], "acted before approval")
                    self.assertEqual(run.state, RunState.WAITING_HUMAN)
                    await eng.confirm(ConfirmArgs(run_id=run.id, confirmation_id=run.pending_confirmation["id"],
                                                  approved=approve))
                    return
                await asyncio.sleep(0.02)
            self.fail("no confirmation requested")
        return await self.run_goal(eng, until=answer)

    async def test_destructive_action_waits_for_approval(self):
        run, _ = await self.confirm_flow(True)
        self.assertEqual(run.state, RunState.SUCCEEDED)
        self.assertEqual(len(self.browser.acts), 1)

    async def test_rejected_action_is_never_performed(self):
        run, _ = await self.confirm_flow(False)
        self.assertEqual(run.state, RunState.STOPPED)
        self.assertEqual(self.browser.acts, [])

    async def test_start_run_validation(self):
        g = goal([{"id": "go", "action": "click", "target": CLICK, "expect": [fact("ok")]}], [fact("ok")])
        eng = self.engine([g])
        with self.assertRaises(PermissionError):
            await eng.start_run(StartRunArgs(tab_id=1, goal_id="g", granted_origins=[]))
        with self.assertRaises(ValueError):
            await eng.start_run(StartRunArgs(tab_id=1, goal_id="nope", granted_origins=[ORIGIN]))
        with self.assertRaises(RuntimeError):  # no planner configured
            await eng.start_run(StartRunArgs(tab_id=1, goal_text="do something", granted_origins=[ORIGIN]))

    async def test_secret_inputs_never_reach_events_or_logs(self):
        g = goal([{"id": "pw", "action": "type", "target": {"label": "Password"}, "value": "{{inputs.pw}}"}],
                 [fact("in")], inputs={"pw": {"secret": True}})
        self.browser.target_context = {"role": "textbox", "name": "Password", "type": "password"}
        self.browser.on_act = lambda a, l: self.browser.facts.update({"value:hunter2-secret": True, "in": True})
        eng = self.engine([g], safety={"require_confirmation": False})
        run, _ = await self.run_goal(eng, inputs={"pw": "hunter2-secret"})
        self.assertEqual(run.state, RunState.SUCCEEDED, run.error)
        blob = repr(self.events) + "".join(p.read_text(encoding="utf-8") for p in (self.tmp / "runs").rglob("*.jsonl"))
        self.assertNotIn("hunter2-secret", blob)


if __name__ == "__main__":
    unittest.main()
