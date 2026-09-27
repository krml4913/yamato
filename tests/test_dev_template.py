"""The dev template's team (reviewer / planner / pm / impl) and the ``merger`` trust profile.

Owner's decisions D-010 (pm only assigns; reviewer reviews and merges; planner talks requirements) and
D-026 (case B: the seat that types ``yamato pr merge`` gets it in its allow list, because the auto-mode
classifier stops it as a "merge without review" now and then) are the template's defaults."""
import re
import unittest

from tests.helpers import ShipTestCase
from tests.test_research import rule_matches
from yamato import runtime
from yamato.util import YAMATO_BIN

MERGE_ALLOW = f"Bash({YAMATO_BIN} pr merge*)"


class DevTemplateTest(ShipTestCase):
    def settings(self, seat):
        return runtime.build_settings(self.shipdir, self.team(), seat)["permissions"]

    def role_text(self, role):
        return (self.shipdir / "roles" / f"{role}.md").read_text(encoding="utf-8")

    def test_the_team_is_pm_impl_reviewer_and_planner(self):
        team = self.team()
        self.assertEqual(list(team["seats"]), ["pm", "impl", "reviewer", "planner"])
        self.assertEqual({r: (s["model"], s["shift"]) for r, s in team["seats"].items()},
                         {"pm": ("opus", "persistent"), "impl": ("sonnet", "per_task"),
                          "reviewer": ("opus", "per_task"), "planner": ("opus", "persistent")})
        self.assertEqual(team["hub"], "pm")
        for role in team["roles"]:
            self.assertTrue((self.shipdir / "roles" / f"{role}.md").is_file(), role)

    def test_trust_and_remote_control(self):
        roles = self.team()["roles"]
        self.assertEqual(roles["pm"]["trust"], "clean")
        self.assertEqual(roles["reviewer"]["trust"], "merger")
        self.assertIsNone(roles["planner"].get("trust"))
        self.assertIsNone(roles["impl"].get("trust"))
        self.assertTrue(roles["planner"].get("remote_control"))   # the owner talks to it from the phone
        self.assertTrue(roles["pm"].get("remote_control"))

    def test_only_the_reviewer_may_merge_without_the_classifier(self):
        self.assertIn(MERGE_ALLOW, self.settings("reviewer")["allow"])
        for seat in ("pm", "impl", "planner"):
            with self.subTest(seat=seat):
                self.assertFalse([r for r in self.settings(seat)["allow"] if "pr merge" in r], seat)

    def test_the_reviewer_is_clean_and_raw_gh_merge_stays_denied(self):
        for seat in ("pm", "reviewer"):
            deny = self.settings(seat)["deny"]
            self.assertIn("WebFetch", deny)
            self.assertIn("WebSearch", deny)
        for seat in ("pm", "impl", "reviewer", "planner"):
            self.assertIn("Bash(gh pr merge*)", self.settings(seat)["deny"], seat)
            self.assertIn("Bash(gh pr create*)", self.settings(seat)["deny"], seat)

    def test_the_prompts_merge_command_is_what_the_allow_rule_covers(self):
        """Claude Code's Bash allow is a text match: the command the prompt tells the reviewer to
        type must start with what the allow rule spells, or the classifier is back."""
        raw = self.role_text("reviewer")
        rendered = runtime.render_prompt(raw, self.shipdir, self.team())
        (rule,) = [r for r in self.settings("reviewer")["allow"] if "pr merge" in r]
        self.assertEqual(rule, MERGE_ALLOW)
        # the profile as written in team.yaml and the prompt use the same `{{yamato}} pr merge`
        self.assertIn("allow:\n      - \"Bash({{yamato}} pr merge*)\"",
                      (self.shipdir / "team.yaml").read_text(encoding="utf-8"))
        self.assertIn("{{yamato}} pr merge", raw)
        commands = re.findall(r"`(" + re.escape(str(YAMATO_BIN)) + r" pr merge [^`]*)`", rendered)
        self.assertTrue(commands)
        for cmd in commands:
            with self.subTest(cmd=cmd):
                self.assertTrue(rule_matches(rule, "Bash", cmd), cmd)
        # the concrete command a reviewer types
        self.assertTrue(rule_matches(rule, "Bash", f"{YAMATO_BIN} pr merge {self.shipdir} T-001 --by reviewer"))
        self.assertFalse(rule_matches(rule, "Bash", "gh pr merge 5"))

    def test_only_the_reviewer_merges_in_the_prompts(self):
        self.assertIn("{{yamato}} pr merge", self.role_text("reviewer"))
        for role in ("pm", "planner"):
            with self.subTest(role=role):
                self.assertNotIn("pr merge {{ship}}", self.role_text(role))
        self.assertIn("reviewer が `yamato pr merge` で行う", self.role_text("impl"))
        pm = self.role_text("pm")
        self.assertIn("reviewer", pm)
        self.assertIn("planner", pm)
        self.assertIn("自分では実装もレビューも merge もしません", pm)

    def test_the_prompts_carry_nothing_of_the_yamato_repo(self):
        for role in ("pm", "impl", "reviewer", "planner"):
            with self.subTest(role=role):
                text = self.role_text(role)
                for word in ("docs/design", "mechanism-not-policy", "yamato の向かう方向", "yamato-dev"):
                    self.assertNotIn(word, text)

    def test_decisions_table(self):
        d = self.team()["decisions"]
        self.assertEqual({c: v["decider"] for c, v in d.items()},
                         {"merge": "reviewer", "design": "planner", "scope_change": "owner", "default": "pm"})
        self.assertIn("設計の根幹に触る PR は owner に上げる", d["merge"]["when"])


if __name__ == "__main__":
    unittest.main()
