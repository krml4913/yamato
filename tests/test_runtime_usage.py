import json
import unittest

from tests.helpers import ShipTestCase
from yamato import runtime, usage
from yamato.util import YAMATO_BIN


class RuntimeTest(ShipTestCase):
    def test_settings_follow_the_verified_template(self):
        team = self.team()
        runtime.generate(self.shipdir, team)
        s = json.loads(runtime.settings_path(self.shipdir, "impl").read_text())
        self.assertEqual(s["crossSessionInbound"], "accept")
        self.assertEqual(s["permissions"]["defaultMode"], "auto")
        self.assertEqual(s["permissions"]["allow"], [f"Bash({YAMATO_BIN} seat-stop:*)"])
        self.assertNotIn("ask", s["permissions"])
        deny = s["permissions"]["deny"]
        for rule in ("Bash(git push --force*)", "Bash(claude stop*)", "Edit(.claude/**)",
                     "Bash(git push*)", "Bash(gh pr create*)", f"Edit(/{self.shipdir}/roster.json)"):
            self.assertIn(rule, deny)
        self.assertEqual(s["worktree"], {"bgIsolation": "none"})
        cmds = [h["command"] for ev in s["hooks"].values() for grp in ev for h in grp["hooks"]]
        self.assertTrue(all(c.startswith(str(YAMATO_BIN) + " hook ") for c in cmds))
        self.assertTrue(all(c.endswith(f"{self.shipdir} impl") for c in cmds))
        waiter = s["hooks"]["Stop"][0]["hooks"][1]
        self.assertTrue(waiter["async"] and waiter["asyncRewake"])
        self.assertIn("wait-deadline", waiter["command"])
        self.assertTrue(set(s["hooks"]) >= {"SessionStart", "Stop", "PermissionRequest", "PermissionDenied"})

    def test_agents_json_renders_placeholders(self):
        team = self.team()
        runtime.generate(self.shipdir, team)
        agents = json.loads(runtime.agents_path(self.shipdir).read_text())
        self.assertEqual(set(agents), {"pm", "impl"})
        self.assertEqual(agents["impl"]["model"], "sonnet")
        for p in (agents["pm"]["prompt"], agents["impl"]["prompt"]):
            self.assertNotIn("{{", p)
            self.assertIn(str(YAMATO_BIN), p)
            self.assertIn(str(self.shipdir), p)
            self.assertIn("SendMessage", p)
            self.assertIn("seat-stop", p)
            self.assertIn("push", p)
        team_json = json.loads((self.shipdir / ".runtime" / "team.json").read_text())
        self.assertEqual(list(team_json["seats"]), ["pm", "impl"])


def _line(ts, mid, **u):
    return json.dumps({"type": "assistant", "timestamp": ts,
                       "message": {"id": mid, "model": "claude-sonnet-5", "usage": u}})


class UsageTest(ShipTestCase):
    def test_count_dedupes_and_windows(self):
        proj = self.config / "projects" / "-ws"
        proj.mkdir(parents=True)
        sid = "0123abcd-0000"
        lines = [
            _line("2026-09-26T00:00:00Z", "m0", input_tokens=999, output_tokens=999),  # before the shift
            _line("2026-09-26T01:00:00Z", "m1", input_tokens=10, output_tokens=5, cache_read_input_tokens=100),
            _line("2026-09-26T01:00:01Z", "m1", input_tokens=10, output_tokens=7, cache_read_input_tokens=100),
            _line("2026-09-26T01:00:02Z", "m2", input_tokens=1, output_tokens=2, cache_creation_input_tokens=50),
            json.dumps({"type": "user", "timestamp": "2026-09-26T01:00:03Z", "message": {"content": "x"}}),
            "not json",
        ]
        (proj / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")
        sub = proj / sid / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-1.jsonl").write_text(_line("2026-09-26T01:00:04Z", "m3", input_tokens=3, output_tokens=4) + "\n")
        since = usage._epoch("2026-09-26T00:30:00Z")
        line = usage.record(self.shipdir, "impl", session_id=sid, shift_no=1, since=since,
                            until=usage._epoch("2026-09-26T02:00:00Z"))
        self.assertEqual((line["input_tokens"], line["output_tokens"]), (14, 13))
        self.assertEqual(line["cache_read_input_tokens"], 100)
        self.assertEqual(line["cache_creation_input_tokens"], 50)
        self.assertEqual(line["messages"], 3)
        self.assertEqual(line["total_tokens"], 177)
        saved = [json.loads(x) for x in (self.shipdir / "usage.jsonl").read_text().splitlines()]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["seat"], "impl")
        self.assertIn("計 177", usage.summary(line))


if __name__ == "__main__":
    unittest.main()
