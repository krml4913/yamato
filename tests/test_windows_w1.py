"""W1: the entry point and character encodings on Windows + Git Bash (T-029,
work/windows-research.md §2.1 / §2.2).

Windows is never available on this machine, so each OS branch is exercised by patching
``sys.platform`` / ``os.name`` / ``os.altsep`` around the one call under test (the way
tests/test_util.py does for W2), and by asserting what the code hands to ``subprocess`` /
``open`` rather than by running it."""
import io
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ShipTestCase
from tests.test_util import WindowsSimTestCase
from yamato import claude, headless, inbox, pr, runtime, util, worktree
from yamato.util import YAMATO_BIN, YamatoError

REPO = Path(__file__).resolve().parents[1]


def run_yamato(*args, env=None):
    """``python <repo>/yamato <args>`` as a real process; bytes out, so the encoding is visible."""
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, str(YAMATO_BIN), *args], capture_output=True, env=e,
                          stdin=subprocess.DEVNULL, timeout=60)


class GitattributesTest(unittest.TestCase):
    def test_lf_is_pinned_for_the_entry_script_and_the_text_files(self):
        lines = (REPO / ".gitattributes").read_text(encoding="utf-8").splitlines()
        for pattern in ("yamato", "*.py", "*.md", "*.yaml", "*.json*"):
            with self.subTest(pattern=pattern):
                self.assertIn(f"{pattern} text eol=lf", lines)


class InvocationTest(ShipTestCase):
    def test_yamato_is_the_interpreter_plus_the_script(self):
        self.assertEqual(shlex.split(runtime.yamato_invocation()), [sys.executable, str(YAMATO_BIN)])

    def test_a_path_with_a_space_stays_one_word_each(self):
        with mock.patch.object(runtime.sys, "executable", "/opt/my python/bin/python3"):
            inv = runtime.yamato_invocation()
        self.assertEqual(shlex.split(inv), ["/opt/my python/bin/python3", str(YAMATO_BIN)])

    def test_windows_style_paths_survive_the_quoting(self):
        # single quotes keep backslashes in bash (Git Bash), so the seat can paste it as is
        with mock.patch.object(runtime.sys, "executable", "C:\\Program Files\\Python311\\python.exe"):
            inv = runtime.yamato_invocation()
        self.assertEqual(shlex.split(inv)[0], "C:\\Program Files\\Python311\\python.exe")

    def test_prompt_and_permission_rules_spell_the_same_command(self):
        team = self.team()
        prompt = runtime.render_prompt("run `{{yamato}} seat-stop {{ship}} x`", self.shipdir, team)
        rule = runtime.render_rule("Bash({{yamato}} seat-stop:*)", self.shipdir, "impl")
        inv = runtime.yamato_invocation()
        self.assertEqual(prompt, f"run `{inv} seat-stop {self.shipdir} x`")
        self.assertEqual(rule, f"Bash({inv} seat-stop:*)")
        self.assertNotIn("{{", prompt + rule)

    def test_hooks_are_exec_form_with_the_interpreter_as_command(self):
        # a Windows path with a space is one command, not quoted or split: no shell is involved
        exe = "C:\\Program Files\\Python311\\python.exe"
        with mock.patch.object(runtime.sys, "executable", exe):
            s = runtime.build_settings(self.shipdir, self.team(), "impl")
        hooks = [h for ev in s["hooks"].values() for grp in ev for h in grp["hooks"]]
        self.assertTrue(hooks)
        for h in hooks:
            with self.subTest(args=h["args"]):
                self.assertEqual(h["type"], "command")
                self.assertEqual(h["command"], exe)
                self.assertEqual(h["args"][:2], [str(YAMATO_BIN), "hook"])
                self.assertEqual(h["args"][-2:], [str(self.shipdir), "impl"])


class ResolveShipTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.ship = self.tmp / "s\\hip"          # a legal name on POSIX; a separator on Windows
        self.ship.mkdir()
        (self.ship / "team.yaml").write_text("name: x\n")
        self.old = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.old)
        env = mock.patch.dict(os.environ, {"YAMATO_HOME": str(self.tmp / "home")})
        env.start()
        self.addCleanup(env.stop)

    def test_a_backslash_ref_is_a_name_on_posix(self):
        with self.assertRaises(YamatoError):
            util.resolve_ship("s\\hip")

    def test_a_backslash_ref_is_a_path_where_backslash_is_a_separator(self):
        with mock.patch.object(os, "altsep", "\\"):
            self.assertEqual(util.resolve_ship("s\\hip"), self.ship)

    def test_a_plain_name_stays_a_name(self):
        with mock.patch.object(os, "altsep", "\\"):
            with self.assertRaises(YamatoError) as cm:
                util.resolve_ship("shipname")
        self.assertIn(str(self.tmp / "home" / "shipname"), str(cm.exception))


class TryLockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "x.lock"

    def test_posix_second_holder_is_refused_until_the_first_lets_go(self):
        a, b = open(self.path, "a"), open(self.path, "a")
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        self.assertTrue(util.try_lock_file(a))
        self.assertFalse(util.try_lock_file(b))
        util.unlock_file(a)
        self.assertTrue(util.try_lock_file(b))
        util.unlock_file(b)


class TryLockWindowsTest(WindowsSimTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "x.lock"

    def test_second_holder_is_refused_via_msvcrt_not_fcntl(self):
        a, b = open(self.path, "a"), open(self.path, "a")
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        with self.as_windows():
            self.assertTrue(util.try_lock_file(a))
            self.assertFalse(util.try_lock_file(b))
            util.unlock_file(a)
            self.assertTrue(util.try_lock_file(b))
            util.unlock_file(b)


class HeadlessLocksTest(ShipTestCase):
    def test_the_run_lock_excludes_a_second_wrapper(self):
        path = headless._lock_path(self.shipdir, "impl", "lock")
        f = headless._try_lock(path)
        self.assertIsNotNone(f)
        self.assertIsNone(headless._try_lock(path))
        self.assertTrue(headless.running(self.shipdir, "impl"))
        headless._release(f)
        self.assertFalse(headless.running(self.shipdir, "impl"))

    def test_headless_and_cli_import_where_fcntl_does_not_exist(self):
        # the whole point of W1's entry check: no module-level `import fcntl` may be left on the
        # import path of a command (Windows has no fcntl, so every command would not start)
        code = ("import sys; sys.modules['fcntl'] = None\n"
                f"sys.path[:0] = [{str(REPO / 'src')!r}, {str(REPO / 'vendor')!r}]\n"
                "import yamato.cli, yamato.headless, yamato.hooks, yamato.seat")
        cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual((cp.returncode, cp.stderr), (0, ""))


class ClaudeBinTest(unittest.TestCase):
    WIN_CMD = "C:\\Users\\x\\AppData\\Roaming\\npm\\claude.cmd"
    WIN_EXE = "C:\\Users\\x\\.local\\bin\\claude.exe"

    def resolve(self, found, platform="win32", name="claude"):
        with mock.patch.dict(os.environ, {"YAMATO_CLAUDE": name}), \
                mock.patch.object(claude.shutil, "which", return_value=found) as which, \
                mock.patch.object(claude.sys, "platform", platform):
            argv = claude._claude_argv(["agents", "--json"])
        which.assert_called_with(name)
        return argv

    def test_claude_is_looked_up_on_the_path(self):
        self.assertEqual(self.resolve(self.WIN_EXE), [self.WIN_EXE, "agents", "--json"])

    def test_a_cmd_shim_goes_through_cmd_on_windows(self):
        self.assertEqual(self.resolve(self.WIN_CMD), ["cmd", "/c", self.WIN_CMD, "agents", "--json"])
        self.assertEqual(self.resolve(self.WIN_CMD.replace("cmd", "BAT")), [
            "cmd", "/c", self.WIN_CMD.replace("cmd", "BAT"), "agents", "--json"])

    def test_a_cmd_name_means_nothing_off_windows(self):
        self.assertEqual(self.resolve(self.WIN_CMD, platform="darwin"), [self.WIN_CMD, "agents", "--json"])

    def test_an_unresolved_name_is_kept_so_the_not_found_error_still_names_it(self):
        self.assertEqual(self.resolve(None, name="my-claude"), ["my-claude", "agents", "--json"])
        with mock.patch.dict(os.environ, {"YAMATO_CLAUDE": "no-such-claude-xyz"}):
            with self.assertRaises(YamatoError) as cm:
                claude.agents()
        self.assertIn("no-such-claude-xyz", str(cm.exception))

    def test_headless_argv_gets_the_same_shim_treatment(self):
        kw = dict(session_id="s", name="n", role="r", agents_json="{}", model="m", settings="/s.json",
                  add_dir="/d", prompt="go")
        with mock.patch.dict(os.environ, {"YAMATO_CLAUDE": "claude"}), \
                mock.patch.object(claude.shutil, "which", return_value=self.WIN_CMD), \
                mock.patch.object(claude.sys, "platform", "win32"):
            argv = claude.headless_argv(**kw)
        self.assertEqual(argv[:4], ["cmd", "/c", self.WIN_CMD, "-p"])
        self.assertEqual(argv[-3:], ["/d", "--", "go"])
        with mock.patch.dict(os.environ, {"YAMATO_CLAUDE": "claude"}), \
                mock.patch.object(claude.shutil, "which", return_value="/usr/bin/claude"):
            argv = claude.headless_argv(**kw)
        self.assertEqual(argv[:2], ["/usr/bin/claude", "-p"])


class ChildOutputIsUtf8Test(unittest.TestCase):
    """Every ``subprocess.run(text=True)`` of yamato decodes as UTF-8 (a Japanese Windows'
    locale is cp932, so ``git`` / ``gh`` / ``claude agents --json`` output would garble or raise)."""

    def assert_utf8(self, run):
        kw = run.call_args.kwargs
        self.assertEqual((kw.get("encoding"), kw.get("errors")), ("utf-8", "replace"), kw)

    def completed(self, out="ok"):
        return subprocess.CompletedProcess([], 0, out, "")

    def test_claude(self):
        with mock.patch.object(claude.subprocess, "run", return_value=self.completed("[]")) as run:
            claude._run(["agents", "--json"])
        self.assert_utf8(run)

    def test_git_root_and_main_repo_root(self):
        for fn in (claude.git_root, claude.main_repo_root):
            with self.subTest(fn=fn.__name__), \
                    mock.patch.object(claude.subprocess, "run", return_value=self.completed("/x\n")) as run:
                fn(Path("/x"))
                self.assert_utf8(run)

    def test_git_of_worktree(self):
        with mock.patch.object(worktree.subprocess, "run", return_value=self.completed()) as run:
            worktree.git(["status"], Path("/x"))
        self.assert_utf8(run)

    def test_gh(self):
        with mock.patch.object(pr.subprocess, "run", return_value=self.completed()) as run:
            pr.gh(["pr", "list"], Path("/x"))
        self.assert_utf8(run)

    def test_notify_command_and_osascript(self):
        from yamato import notify

        with mock.patch.object(notify.subprocess, "run", return_value=self.completed()) as run:
            notify._send_command({}, {"command": "cat"}, "t", "m", "info")
        self.assert_utf8(run)
        with mock.patch.object(notify.subprocess, "run", return_value=self.completed()) as run, \
                mock.patch.object(notify.platform, "system", return_value="Darwin"):
            notify._send_mac({}, {}, "t", "m", "info")
        self.assert_utf8(run)

    def test_headless_ps(self):
        with mock.patch.object(headless.subprocess, "run", return_value=self.completed("claude --session-id abc")) as run:
            headless.live_pid({"pid": 123, "sessionId": "abc"})
        self.assert_utf8(run)


class EntryPointUtf8Test(unittest.TestCase):
    def test_the_entry_point_writes_utf8_even_when_the_locale_is_not(self):
        # a Japanese Windows console is cp932: without the reconfigure, this message would come out as cp932
        with tempfile.TemporaryDirectory() as home:
            cp = run_yamato("inbox", "no-such-ship", "impl",
                            env={"YAMATO_HOME": home, "PYTHONIOENCODING": "cp932", "PYTHONUTF8": "0"})
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("艦が見つかりません", cp.stderr.decode("utf-8"))


class WritesKeepLfTest(ShipTestCase):
    """Text-mode writes turn ``\\n`` into ``\\r\\n`` on Windows: every write of a record says ``newline="\\n"``."""

    def opens_with_lf(self, call):
        real = open
        seen = []

        def spy(*a, **kw):
            seen.append(kw)
            return real(*a, **kw)

        with mock.patch("builtins.open", spy):
            call()
        # the lock file's bare `open(path, "a")` never gets a write; record writes name an encoding
        records = [kw for kw in seen if "encoding" in kw]
        self.assertTrue(records)
        self.assertTrue(all(kw.get("newline") == "\n" for kw in records), records)

    def test_atomic_write(self):
        seen = []
        real = os.fdopen

        def spy(*a, **kw):
            seen.append(kw)
            return real(*a, **kw)

        with mock.patch.object(util.os, "fdopen", spy):
            util.atomic_write(self.tmp / "a" / "x.md", "a\nb\n")
        self.assertEqual([kw.get("newline") for kw in seen], ["\n"])
        self.assertEqual((self.tmp / "a" / "x.md").read_bytes(), b"a\nb\n")

    def test_append_log(self):
        self.opens_with_lf(lambda: util.append_log(self.shipdir, "impl", "x"))

    def test_inbox_append(self):
        self.opens_with_lf(lambda: inbox.append(self.shipdir, "impl", "pm", "hi"))


if __name__ == "__main__":
    unittest.main()
