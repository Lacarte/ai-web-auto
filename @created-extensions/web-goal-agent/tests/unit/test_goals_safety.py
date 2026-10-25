"""Goal spec validation/rendering, the confirmation classifier, and log redaction."""

from __future__ import annotations

import unittest

from helpers import ORIGIN, goal

from goal_agent_backend.goals import GoalError, GoalSpec, Locator, describe_locator, render
from goal_agent_backend.runlog import MASK, Redactor
from goal_agent_backend.safety import classify


class TestGoalSpecs(unittest.TestCase):
    def test_rejects_specs_that_cannot_be_verified_or_are_brittle(self):
        bad = [
            ({"role": "button", "xpath": "/html/body/div[2]/button"}, "absolute xpath"),
            ({"name": "Save"}, "name without role"),
            ({}, "no strategy"),
            ({"any": [{"role": "button"}], "role": "link"}, "any mixed with strategies"),
        ]
        for loc, why in bad:
            with self.subTest(why), self.assertRaises(ValueError):
                Locator.model_validate(loc)
        with self.assertRaisesRegex(ValueError, "must declare 'expect'"):
            GoalSpec.model_validate(goal([{"id": "c", "action": "click", "target": {"text": "Go"}}], [{"url": {"changed": True}}]))
        with self.assertRaisesRegex(ValueError, "outside allowed_origins"):
            GoalSpec.model_validate(goal([{"id": "n", "action": "navigate", "url": "https://evil.example/"}],
                                         [{"url": {"changed": True}}]))
        with self.assertRaises(ValueError):  # a goal needs success criteria
            GoalSpec.model_validate(goal([{"id": "w", "action": "wait", "expect": [{"title": "x"}]}], []))

    def test_render_substitutes_inputs_and_tracks_secrets(self):
        spec = GoalSpec.model_validate(goal(
            [{"id": "e", "action": "click", "target": {"role": "button", "name": "Edit", "within": {"role": "row", "name": {"contains": "{{inputs.who}}"}}},
              "expect": [{"dialog_open": {"name": "Edit {{inputs.who}}"}}]},
             {"id": "p", "action": "type", "target": {"label": "Password"}, "value": "{{inputs.pw}}"}],
            [{"text_present": {"text": "{{inputs.who}}"}}],
            inputs={"who": {"default": "Ada"}, "pw": {"secret": True}}))
        rendered, values, secrets = render(spec, {"pw": "s3cret"})
        self.assertEqual(rendered.steps[0].target.within.name.contains, "Ada")
        self.assertEqual(rendered.steps[0].expect[0].args.name.equals, "Edit Ada")
        self.assertEqual(secrets, {"s3cret"})
        with self.assertRaisesRegex(GoalError, "required"):
            render(spec, {})
        with self.assertRaisesRegex(GoalError, "unknown inputs"):
            render(spec, {"pw": "x", "bogus": "y"})

    def test_expectation_wire_resolves_target_reference(self):
        spec = GoalSpec.model_validate(goal(
            [{"id": "c", "action": "check", "target": {"label": "Agree"}, "checked": True,
              "expect": [{"attribute": {"target": "$target", "name": "aria-checked", "equals": "true"}}]}],
            [{"checked": {"target": {"label": "Agree"}}}]))
        step = spec.steps[0]
        wire = step.expect[0].wire(step.target)
        self.assertEqual(wire["args"]["target"], {"label": {"equals": "Agree", "case_sensitive": False}})
        self.assertEqual(spec.success[0].wire()["args"]["value"], True)

    def test_describe_locator_reads_naturally(self):
        loc = Locator.model_validate({"role": "button", "name": "Save", "within": {"role": "dialog", "name": "Edit customer"}})
        self.assertEqual(describe_locator(loc), 'the "Save" button inside the "Edit customer" dialog')


class TestConfirmationClassifier(unittest.TestCase):
    def test_sensitive_actions(self):
        cases = [
            ("click", {"role": "button", "name": "Delete"}, "destructive"),
            ("click", {"role": "button", "name": "Place order"}, "financial"),
            ("click", {"role": "button", "name": "Post comment"}, "outward"),
            ("click", {"role": "button", "name": "OK", "dialog_name": "Remove 3 files?", "dialog_role": "alertdialog"}, "destructive"),
            ("click", {"role": "button", "name": "Continue", "form": {"has_payment_fields": True}, "submits": True}, "financial"),
            ("type", {"role": "textbox", "type": "password"}, "credential"),
            ("type", {"role": "textbox", "payment_field": True}, "financial"),
        ]
        for action, target, category in cases:
            with self.subTest(target=target):
                s = classify(action, target, None)
                self.assertIsNotNone(s)
                self.assertEqual(s.category, category)

    def test_ordinary_actions_and_popup_openers_pass(self):
        for action, target in [
            ("click", {"role": "button", "name": "Save"}),
            ("click", {"role": "button", "name": "Edit"}),
            ("click", {"role": "button", "name": "Delete", "opens_popup": "dialog"}),  # opens the confirm dialog
            ("type", {"role": "textbox", "type": "text"}),
        ]:
            with self.subTest(target=target):
                self.assertIsNone(classify(action, target, None))
        self.assertEqual(classify("click", {"name": "Save"}, True).category, "declared")


class TestRedaction(unittest.TestCase):
    def test_secrets_and_credential_keys_are_masked(self):
        r = Redactor({"hunter2"})
        out = r.obj({"text": "typed hunter2 into field", "token": "abc", "nested": [{"password": "x"}], "n": 3})
        self.assertEqual(out["text"], f"typed {MASK} into field")
        self.assertEqual(out["token"], MASK)
        self.assertEqual(out["nested"][0]["password"], MASK)
        self.assertEqual(out["n"], 3)


if __name__ == "__main__":
    unittest.main()
