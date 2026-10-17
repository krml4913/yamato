"""the claude command line stays under the Windows limits (or fails with a clear error)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yamato import claude
from yamato.util import YamatoError

WIN_CMD = "C:\\Users\\x\\AppData\\Roaming\\npm\\claude.cmd"
WIN_EXE = "C:\\Users\\x\\.local\\bin\\claude.exe"


def agents_of(sizes):
    return json.dumps({r: {"description": "d", "prompt": "あ" * n, "model": "m"} for r, n in sizes.items()},
                      ensure_ascii=False)


def headless(agents_json, role="impl", prompt="go"):
    return claude.headless_argv(session_id="s", name="n", role=role, agents_json=agents_json, model="m",
                                settings="/s.json", add_dir="/d", prompt=prompt)


class OnlyRoleTest(unittest.TestCase):
    def test_launch_and_headless_pass_only_the_seats_role(self):
        agents = agents_of({"pm": 10000, "impl": 7000, "reviewer": 6000})
        argv = headless(agents)
        sent = json.loads(argv[argv.index("--agents") + 1])
        self.assertEqual(list(sent), ["impl"])
        seen = []
        with mock.patch.object(claude, "_run", lambda a, **kw: seen.append(a) or mock.Mock(returncode=1, stdout="", stderr="")):
            with self.assertRaises(YamatoError):
                claude.launch(cwd="/w", name="n", role="impl", agents_json=agents, model="m",
                              settings="/s.json", add_dir="/d", prompt="go")
        self.assertEqual(list(json.loads(seen[0][seen[0].index("--agents") + 1])), ["impl"])

    def test_unknown_role_or_bad_json_is_left_alone(self):
        self.assertEqual(claude.only_role("{}", "r"), "{}")
        self.assertEqual(claude.only_role("not json", "r"), "not json")


class WindowsLimitTest(unittest.TestCase):
    def argv(self, found, agents, platform="win32"):
        with mock.patch.dict(os.environ, {"YAMATO_CLAUDE": "claude"}), \
                mock.patch("shutil.which", return_value=found), \
                mock.patch.object(claude.sys, "platform", platform):
            return headless(agents)

    def test_over_the_cmd_shell_limit_is_a_clear_error(self):
        with self.assertRaises(YamatoError) as cm:
            self.argv(WIN_CMD, agents_of({"impl": 9000}))
        self.assertIn("8191", str(cm.exception))
        self.assertIn("大きい引数", str(cm.exception))

    def test_exe_has_the_larger_limit(self):
        self.assertTrue(self.argv(WIN_EXE, agents_of({"impl": 9000})))
        with self.assertRaises(YamatoError) as cm:
            self.argv(WIN_EXE, agents_of({"impl": 40000}))
        self.assertIn("32767", str(cm.exception))

    def test_other_platforms_are_not_checked(self):
        self.assertTrue(self.argv("/usr/bin/claude", agents_of({"impl": 40000}), platform="darwin"))

    def test_npm_shim_runs_node_directly(self):
        with tempfile.TemporaryDirectory() as d:
            shim = Path(d) / "claude.cmd"
            shim.write_text("")
            cli = Path(d) / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
            cli.parent.mkdir(parents=True)
            cli.write_text("")
            with mock.patch.dict(os.environ, {"YAMATO_CLAUDE": str(shim)}), \
                    mock.patch("shutil.which", side_effect=lambda n: str(shim) if n == str(shim) else "/n/node"), \
                    mock.patch.object(claude.sys, "platform", "win32"):
                argv = headless(agents_of({"impl": 9000}))
        self.assertEqual(argv[:3], ["/n/node", str(cli), "-p"])


if __name__ == "__main__":
    unittest.main()
