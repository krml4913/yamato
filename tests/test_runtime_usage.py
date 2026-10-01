import json
import sys
import unittest

from tests.helpers import ShipTestCase
from yamato import runtime, usage
from yamato.util import YAMATO_BIN


class RuntimeTest(ShipTestCase):
    def test_settings_follow_the_verified_template(self):
        team = self.team()
        runtime.generate(self.shipdir, team)
        s = json.loads(runtime.settings_path(self.shipdir, "impl").read_text(encoding="utf-8"))
        self.assertEqual(s["crossSessionInbound"], "accept")
        self.assertEqual(s["permissions"]["defaultMode"], "auto")
        self.assertEqual(s["permissions"]["allow"], [f"Bash({runtime.yamato_invocation()} seat-stop:*)"])
        self.assertNotIn("ask", s["permissions"])
        deny = s["permissions"]["deny"]
        # the deny list comes from the template's team.yaml ({{ship}} expanded), not from code
        for rule in ("Bash(git push --force*)", "Bash(git reset --hard*)", "Bash(claude stop*)", "Edit(.claude/**)",
                     "Bash(gh pr create*)", f"Edit(/{runtime.rule_path(self.shipdir)}/roster.json)"):
            self.assertIn(rule, deny)
        self.assertEqual(s["worktree"], {"bgIsolation": "none"})  # template `settings:`
        # exec form (W1): no shell string. command = the interpreter, args = script + "hook <event> <ship> <seat>"
        hooks = [h for ev in s["hooks"].values() for grp in ev for h in grp["hooks"]]
        self.assertTrue(all(h["command"] == sys.executable for h in hooks))
        self.assertTrue(all(h["args"][:2] == [str(YAMATO_BIN), "hook"] for h in hooks))
        self.assertTrue(all(h["args"][-2:] == [str(self.shipdir), "impl"] for h in hooks))
        waiter = s["hooks"]["Stop"][0]["hooks"][1]
        self.assertTrue(waiter["async"] and waiter["asyncRewake"])
        self.assertIn("wait-deadline", waiter["args"])
        self.assertTrue(set(s["hooks"]) >= {"SessionStart", "UserPromptSubmit", "Stop", "PermissionRequest", "PermissionDenied"})

    def test_dev_template_keeps_web_away_from_the_merge_role(self):
        # design-drift #3: pm (merge の権限を持つ captain) は開発艦の既定で trust: clean。
        # impl には付けない (付けるかは艦ごと)
        team = self.team()
        runtime.generate(self.shipdir, team)
        pm_deny = json.loads(runtime.settings_path(self.shipdir, "pm").read_text(encoding="utf-8"))["permissions"]["deny"]
        self.assertIn("WebFetch", pm_deny)
        self.assertIn("WebSearch", pm_deny)
        impl_deny = json.loads(runtime.settings_path(self.shipdir, "impl").read_text(encoding="utf-8"))["permissions"]["deny"]
        self.assertNotIn("WebFetch", impl_deny)
        self.assertNotIn("WebSearch", impl_deny)

    def test_env_unset_blanked_in_settings_env(self):
        # e2e-p1 D: a bg seat inherits the daemon's environment; `env -u` at launch misses it.
        # D-003: env_unset の既定は空なので、この艦は明示して使う (道具としては残る)
        team = self.team()
        team["env_unset"] = ["GH_TOKEN", "GITHUB_TOKEN"]
        team["settings"] = {"env": {"GH_TOKEN": "leak", "YAMATO_GH": "/x/gh"}}
        runtime.generate(self.shipdir, team)
        env = json.loads(runtime.settings_path(self.shipdir, "impl").read_text(encoding="utf-8"))["env"]
        env.pop("CLAUDE_CODE_USE_POWERSHELL_TOOL", None)   # Windows だけの既定 (T-047)
        self.assertEqual(env, {"GH_TOKEN": "", "GITHUB_TOKEN": "", "YAMATO_GH": "/x/gh"})

    def test_policy_is_whatever_team_yaml_says(self):
        import subprocess

        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        team = self.team()
        team["deny"] = []
        team["settings"] = {"worktree": {"bgIsolation": "auto"}, "language": "English",
                            "permissions": {"allow": ["Bash(make test)"], "defaultMode": "manual"},
                            "crossSessionInbound": "hold"}
        s = runtime.build_settings(self.shipdir, team, "impl")
        self.assertEqual(s["permissions"]["deny"], [])
        self.assertEqual(s["worktree"], {"bgIsolation": "auto"})
        self.assertEqual(s["language"], "English")
        self.assertEqual(s["permissions"]["allow"], ["Bash(make test)", f"Bash({runtime.yamato_invocation()} seat-stop:*)"])
        # what the mechanism needs is not overridable
        self.assertEqual(s["permissions"]["defaultMode"], "auto")
        self.assertEqual(s["crossSessionInbound"], "accept")
        self.assertIn("SessionStart", s["hooks"])

    def test_no_repo_or_ship_inside_repo_forces_no_isolation(self):
        import subprocess

        team = self.team()
        team["settings"] = {"worktree": {"bgIsolation": "auto"}}
        # workspace is not a git repo
        self.assertEqual(runtime.build_settings(self.shipdir, team, "impl")["worktree"], {"bgIsolation": "none"})
        # ship folder inside the workspace repo
        subprocess.run(["git", "init", "-q", str(self.tmp)], check=True)
        self.assertEqual(runtime.build_settings(self.shipdir, team, "impl")["worktree"], {"bgIsolation": "none"})

    def test_agents_json_renders_placeholders(self):
        team = self.team()
        runtime.generate(self.shipdir, team)
        agents = json.loads(runtime.agents_path(self.shipdir).read_text(encoding="utf-8"))
        self.assertEqual(set(agents), {"pm", "impl", "reviewer", "planner"})
        self.assertEqual(agents["impl"]["model"], "sonnet")
        for p in (a["prompt"] for a in agents.values()):
            self.assertNotIn("{{", p)
            self.assertIn(runtime.posix_path(YAMATO_BIN), p)
            self.assertIn(runtime.posix_path(self.shipdir), p)
            self.assertIn("SendMessage", p)
            self.assertIn("seat-stop", p)
            self.assertIn("push", p)
        team_json = json.loads((self.shipdir / ".runtime" / "team.json").read_text(encoding="utf-8"))
        self.assertEqual(list(team_json["seats"]), ["pm", "impl", "reviewer", "planner"])


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
        (proj / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        sub = proj / sid / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-1.jsonl").write_text(_line("2026-09-26T01:00:04Z", "m3", input_tokens=3, output_tokens=4) + "\n", encoding="utf-8")
        since = usage._epoch("2026-09-26T00:30:00Z")
        line = usage.record(self.shipdir, "impl", session_id=sid, shift_no=1, since=since,
                            until=usage._epoch("2026-09-26T02:00:00Z"))
        self.assertEqual((line["input_tokens"], line["output_tokens"]), (14, 13))
        self.assertEqual(line["cache_read_input_tokens"], 100)
        self.assertEqual(line["cache_creation_input_tokens"], 50)
        self.assertEqual(line["messages"], 3)
        self.assertEqual(line["total_tokens"], 177)
        saved = [json.loads(x) for x in (self.shipdir / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["seat"], "impl")
        self.assertIn("計 177", usage.summary(line))
        self.assertNotIn("unknown", line)

    def test_usage_unknown_without_a_transcript(self):
        line = usage.record(self.shipdir, "impl", session_id="no-such-session", shift_no=2, since=0)
        self.assertTrue(line["unknown"])
        self.assertIn("分からない", usage.summary(line))


if __name__ == "__main__":
    unittest.main()
