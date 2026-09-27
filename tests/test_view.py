import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from yamato import cli, roster, seat, ship
from yamato.util import YAMATO_BIN, YamatoError, resolve_ship
from yamato.view import attach, layout, opener

from .helpers import ShipTestCase

SID_A = "aaaaaaaa-0000-4000-8000-000000000001"
SID_B = "bbbbbbbb-0000-4000-8000-000000000002"
LIVE_PID = os.getpid()


def start_shift(shipdir: Path, seat_name: str, sid: str) -> None:
    """Write the roster the way P0 does (the view must read what P0 writes)."""
    roster.start_shift(shipdir, seat_name, session_id=sid, short_id=sid[:8],
                       session_name=f"dev.{seat_name}", how="new")


class ShipDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class ResolverTest(ShipDirTest):
    def resolver(self, listing, seat_name="pm"):
        calls = []

        def agents():
            calls.append(1)
            return listing
        return attach.roster_resolver(self.dir, seat_name, agents=agents), calls

    def test_live_session(self):
        start_shift(self.dir, "pm", SID_A)
        r, _ = self.resolver([{"sessionId": SID_A, "id": SID_A[:8], "pid": LIVE_PID}])
        # attach takes the short id; the full sessionId gives "No job matching"
        self.assertEqual(r(), SID_A[:8])

    def test_short_id_falls_back_to_prefix(self):
        start_shift(self.dir, "pm", SID_A)
        r, _ = self.resolver([{"sessionId": SID_A, "pid": LIVE_PID}])
        self.assertEqual(r(), SID_A[:8])

    def test_stopped_session_is_not_attached(self):
        # pid null = stopped; attaching would resurrect it (spike Q4)
        start_shift(self.dir, "pm", SID_A)
        r, _ = self.resolver([{"sessionId": SID_A, "pid": None}])
        self.assertIsNone(r())

    def test_dead_pid_is_not_attached(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        start_shift(self.dir, "pm", SID_A)
        r, _ = self.resolver([{"sessionId": SID_A, "pid": dead.pid}])
        self.assertIsNone(r())

    def test_session_unknown_to_claude(self):
        start_shift(self.dir, "pm", SID_A)
        r, _ = self.resolver([{"sessionId": SID_B, "pid": LIVE_PID}])
        self.assertIsNone(r())

    def test_only_roster_decides_not_the_name(self):
        # another live session with the seat's name is not picked (--name is not unique)
        start_shift(self.dir, "pm", SID_A)
        r, _ = self.resolver([
            {"sessionId": SID_A, "name": "dev.pm", "pid": None},
            {"sessionId": SID_B, "name": "dev.pm", "pid": LIVE_PID},
        ])
        self.assertIsNone(r())

    def test_no_roster_does_not_ask_claude(self):
        r, calls = self.resolver([{"sessionId": SID_A, "pid": LIVE_PID}])
        self.assertIsNone(r())
        self.assertEqual(calls, [])

    def test_broken_roster_means_no_shift(self):
        (self.dir / "roster.json").write_text("{not json")
        r, calls = self.resolver([{"sessionId": SID_A, "pid": LIVE_PID}])
        self.assertIsNone(r())
        self.assertEqual(calls, [])

    def test_other_seats_shift_is_not_picked(self):
        start_shift(self.dir, "impl", SID_A)
        r, calls = self.resolver([{"sessionId": SID_A, "pid": LIVE_PID}], "pm")
        self.assertIsNone(r())
        self.assertEqual(calls, [])

    def test_follows_roster_changes(self):
        listing = [{"sessionId": SID_A, "id": "aaaaaaaa", "pid": LIVE_PID},
                   {"sessionId": SID_B, "id": "bbbbbbbb", "pid": LIVE_PID}]
        r, _ = self.resolver(listing)
        start_shift(self.dir, "pm", SID_A)
        self.assertEqual(r(), "aaaaaaaa")
        start_shift(self.dir, "pm", SID_B)
        self.assertEqual(r(), "bbbbbbbb")

    def test_ended_shift_is_attached_only_while_its_process_is_gone(self):
        # persistent seat: the same sessionId comes back on resume. Off + pid null -> wait;
        # resumed (pid back) -> attach again.
        start_shift(self.dir, "pm", SID_A)
        roster.end_shift(self.dir, "pm", reason="seat-stop")
        listing = [{"sessionId": SID_A, "id": "aaaaaaaa", "pid": None}]
        r, _ = self.resolver(listing)
        self.assertIsNone(r())
        listing[0]["pid"] = LIVE_PID
        self.assertEqual(r(), "aaaaaaaa")


class FakeProc:
    """A `claude attach` that exits after `lives` waits, or when terminated."""

    def __init__(self, argv, lives=None):
        self.argv = argv
        self.lives = lives
        self.returncode = None
        self.terminated = False

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        if self.lives is not None:
            self.lives -= 1
            if self.lives <= 0:
                self.returncode = 0
                return 0
        raise subprocess.TimeoutExpired(self.argv, timeout)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class RunLoopTest(unittest.TestCase):
    def run_loop(self, answers, rounds, lives=None):
        answers = list(answers)
        procs = []

        def resolve():
            a = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(a, Exception):
                raise a
            return a

        def spawn(argv):
            procs.append(FakeProc(argv, lives))
            return procs[-1]

        out = io.StringIO()
        code = attach.run(resolve, "dev.pm", poll=0, out=out, spawn=spawn,
                          sleep=lambda s: None, rounds=rounds)
        return code, procs, out.getvalue()

    def test_waits_when_no_shift(self):
        code, procs, out = self.run_loop([None], rounds=3)
        self.assertEqual(code, 0)
        self.assertEqual(procs, [])
        self.assertIn("待機中", out)

    def test_attaches_with_resolved_id(self):
        _, procs, _ = self.run_loop([SID_A], rounds=1, lives=1)
        self.assertEqual(procs[0].argv[1:], ["attach", SID_A])

    def test_reattaches_when_shift_changes(self):
        # A attached; watcher sees A, then B -> A's attach is stopped, B attached
        _, procs, out = self.run_loop([SID_A, SID_A, SID_B, SID_B], rounds=2, lives=3)
        self.assertEqual([p.argv[-1] for p in procs], [SID_A, SID_B])
        self.assertTrue(procs[0].terminated)
        self.assertIn("付け直し", out)

    def test_keeps_attach_while_new_shift_not_alive(self):
        # roster empty / next shift not up yet: stay on A until its attach ends itself
        _, procs, _ = self.run_loop([SID_A, None, None, None], rounds=1, lives=3)
        self.assertEqual(len(procs), 1)
        self.assertFalse(procs[0].terminated)

    def test_error_while_resolving_keeps_waiting(self):
        code, procs, out = self.run_loop([YamatoError("boom"), None], rounds=2)
        self.assertEqual(code, 0)
        self.assertEqual(procs, [])
        self.assertIn("boom", out)


class LayoutBuildTest(unittest.TestCase):
    def test_tabs_and_panes(self):
        kdl = layout.build([("dev", "/x/dev", ["pm", "impl-1", "reviewer"]),
                            ("research", "/x/research", ["editor"])], "/r/yamato")
        self.assertEqual(kdl.count("tab name="), 2)
        self.assertIn('tab name="dev" focus=true {', kdl)
        self.assertIn('tab name="research" {', kdl)
        self.assertEqual(kdl.count('command="/r/yamato"'), 4)
        self.assertIn('args "view" "attach" "/x/dev" "impl-1"', kdl)
        self.assertIn('args "view" "attach" "/x/research" "editor"', kdl)
        # two seats per row: pm|impl-1 share a vertical split, reviewer is alone
        self.assertEqual(kdl.count('split_direction="vertical"'), 1)
        self.assertEqual(kdl.count("{"), kdl.count("}"))

    def test_quoting(self):
        self.assertEqual(layout.kdl_str('a"b\\c'), '"a\\"b\\\\c"')


class LayoutForTest(ShipTestCase):
    def test_default_command_is_this_repos_yamato(self):
        self.assertTrue(YAMATO_BIN.is_file())
        self.assertIn(f'command="{YAMATO_BIN}"', layout.layout_for(["t1"]))

    def test_panes_get_the_absolute_path_and_the_team_name_tabs(self):
        by_name = layout.layout_for(["t1"], "yamato")
        by_path = layout.layout_for([str(self.shipdir)], "yamato")
        self.assertEqual(by_name, by_path)
        self.assertIn('tab name="t1" focus=true {', by_name)
        self.assertIn(f'args "view" "attach" "{self.shipdir.resolve()}" "pm"', by_name)
        self.assertEqual(by_name.count("pane name="), 2)   # dev template: pm + impl

    def test_seats_come_from_team_yaml_before_up(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("count: 1", "count: 3"))
        kdl = layout.layout_for(["t1"], "yamato")
        for name in ("pm", "impl-1", "impl-2", "impl-3"):
            self.assertIn(f'pane name="{name}"', kdl)
        self.assertNotIn('pane name="impl"', kdl)

    def test_seats_come_from_runtime_team_json_after_up(self):
        seat.prepare(self.shipdir)   # what `yamato up` writes
        team_json = self.shipdir / ".runtime" / "team.json"
        data = json.loads(team_json.read_text())
        self.assertEqual(list(data["seats"]), ["pm", "impl"])
        data["seats"] = {"pm": data["seats"]["pm"], "qa-1": data["seats"]["impl"]}
        team_json.write_text(json.dumps(data))
        kdl = layout.layout_for(["t1"], "yamato")
        self.assertIn('pane name="qa-1"', kdl)
        self.assertNotIn('pane name="impl"', kdl)

    def test_unknown_ship(self):
        with self.assertRaises(YamatoError):
            layout.layout_for(["nope"])


class RegistryTest(ShipTestCase):
    """A ship made with --path is listed in $YAMATO_HOME/ships.json; view finds it there."""

    def setUp(self):
        super().setUp()
        self.elsewhere = self.tmp / "elsewhere" / "fleet-a"
        ship.create("mine", str(self.workspace), str(self.elsewhere), "dev")

    def test_resolve_by_name(self):
        self.assertEqual(resolve_ship("mine"), self.elsewhere.resolve())
        self.assertFalse((Path(os.environ["YAMATO_HOME"]) / "mine").exists())

    def test_layout_by_name(self):
        kdl = layout.layout_for(["mine"], "yamato")
        self.assertIn('tab name="mine" focus=true {', kdl)      # the team's name, not the directory's
        self.assertIn(f'args "view" "attach" "{self.elsewhere.resolve()}" "impl"', kdl)

    def test_attach_by_name(self):
        with mock.patch.object(attach, "run", return_value=0) as run:
            self.assertEqual(cli.main(["view", "attach", "mine", "pm"]), 0)
        resolve, label = run.call_args.args
        self.assertEqual(label, "mine.pm")
        # the resolver it was given reads the roster of the --path ship (and asks the fake claude)
        start_shift(self.elsewhere, "pm", SID_A)
        self.fake_state.write_text(json.dumps(
            {"sessions": [{"sessionId": SID_A, "id": SID_A[:8], "pid": LIVE_PID}], "calls": []}))
        self.assertEqual(resolve(), SID_A[:8])


class FakeZellijRun:
    """Stands in for ``subprocess.run`` in opener tests: records every zellij
    invocation and answers ``list-sessions --short`` from ``sessions``."""

    def __init__(self, sessions: str = ""):
        self.calls: list[list[str]] = []
        self.sessions = sessions

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        out = self.sessions if argv[1:3] == ["list-sessions", "--short"] else ""
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")


class OpenerTest(ShipTestCase):
    """t1 (dev template) lands directly under $YAMATO_HOME, so ``all_ships()`` finds it."""

    def test_no_refs_and_no_ships_is_an_error(self):
        with mock.patch.dict(os.environ, {"YAMATO_HOME": str(self.tmp / "empty-home")}):
            with self.assertRaises(YamatoError):
                opener.open_ships(run=FakeZellijRun())

    def test_default_refs_are_every_registered_ship(self):
        ship.create("other", str(self.workspace), None, "dev")
        run = FakeZellijRun()
        dest = self.tmp / "view.kdl"
        opener.open_ships(command="yamato", output=str(dest), run=run, in_zellij=False)
        text = dest.read_text()
        self.assertIn('tab name="t1"', text)
        self.assertIn('tab name="other"', text)

    def test_outside_zellij_attaches_an_existing_session(self):
        run = FakeZellijRun(sessions="yamato-view [Created ...]\n")
        dest = self.tmp / "view.kdl"
        opener.open_ships(["t1"], command="yamato", output=str(dest), run=run, in_zellij=False)
        self.assertIn('args "view" "attach"', dest.read_text())
        self.assertEqual(run.calls, [
            ["zellij", "list-sessions", "--short"],
            ["zellij", "attach", "yamato-view"],
        ])

    def test_outside_zellij_starts_a_new_session_when_none_is_up(self):
        run = FakeZellijRun(sessions="")
        dest = self.tmp / "view.kdl"
        opener.open_ships(["t1"], command="yamato", output=str(dest), run=run, in_zellij=False,
                          session="my-view")
        self.assertEqual(run.calls, [
            ["zellij", "list-sessions", "--short"],
            ["zellij", "--session", "my-view", "--new-session-with-layout", str(dest)],
        ])

    def test_default_output_is_under_yamato_home(self):
        run = FakeZellijRun(sessions="yamato-view\n")
        opener.open_ships(["t1"], command="yamato", run=run, in_zellij=False)
        self.assertTrue((Path(os.environ["YAMATO_HOME"]) / "view.kdl").is_file())

    def test_in_zellij_adds_one_tab_per_ship(self):
        ship.create("other", str(self.workspace), None, "dev")
        run = FakeZellijRun()
        opener.open_ships(["t1", "other"], command="yamato", run=run, in_zellij=True)
        new_tabs = [c for c in run.calls if c[1:3] == ["action", "new-tab"]]
        self.assertEqual(len(new_tabs), 2)
        for call in new_tabs:
            self.assertEqual(call[0], "zellij")
            self.assertEqual(call[3], "--layout")
            text = Path(call[4]).read_text()
            self.assertEqual(text.count("tab name="), 1)   # one ship, one tab, per file
        # each ship's own tab landed in its own file
        texts = [Path(c[4]).read_text() for c in new_tabs]
        self.assertTrue(any('tab name="t1"' in t for t in texts))
        self.assertTrue(any('tab name="other"' in t for t in texts))

    def test_in_zellij_is_read_from_the_environment_by_default(self):
        run = FakeZellijRun()
        with mock.patch.dict(os.environ, {"ZELLIJ": "0"}):
            opener.open_ships(["t1"], command="yamato", run=run)
        self.assertEqual(run.calls[0][1:3], ["action", "new-tab"])

    def test_custom_zellij_binary(self):
        run = FakeZellijRun()
        with mock.patch.dict(os.environ, {"YAMATO_ZELLIJ": "/opt/zellij"}):
            opener.open_ships(["t1"], command="yamato", run=run, in_zellij=True)
        self.assertEqual(run.calls[0][0], "/opt/zellij")

    def test_missing_zellij_binary_is_a_clean_error(self):
        def boom(argv, **kwargs):
            raise FileNotFoundError()
        with self.assertRaises(YamatoError):
            opener.open_ships(["t1"], command="yamato", run=boom, in_zellij=False,
                              output=str(self.tmp / "view.kdl"))


class ViewCliTest(ShipTestCase):
    def call(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_layout_to_stdout_and_file(self):
        code, out, _ = self.call(["view", "layout", "t1", "--command", "yamato"])
        self.assertEqual(code, 0)
        self.assertIn('args "view" "attach"', out)
        dest = self.tmp / "view.kdl"
        code, out, _ = self.call(["view", "layout", "t1", "--command", "yamato", "-o", str(dest)])
        self.assertEqual((code, out), (0, ""))
        self.assertIn('pane name="pm"', dest.read_text())

    def test_layout_unknown_ship_is_an_error_line_not_a_traceback(self):
        code, _, err = self.call(["view", "layout", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("艦が見つかりません", err)

    def test_attach_unknown_seat_is_refused(self):
        with mock.patch.object(attach, "run") as run:
            code, _, err = self.call(["view", "attach", "t1", "typo"])
        self.assertEqual(code, 1)
        self.assertIn("席がありません", err)
        run.assert_not_called()

    def test_attach_label_and_poll(self):
        with mock.patch.object(attach, "run", return_value=0) as run:
            self.assertEqual(self.call(["view", "attach", "t1", "impl", "--poll", "0.5"])[0], 0)
        self.assertEqual(run.call_args.args[1], "t1.impl")
        self.assertEqual(run.call_args.kwargs["poll"], 0.5)
        with mock.patch.dict(os.environ, {"YAMATO_VIEW_POLL": "7"}), \
                mock.patch.object(attach, "run", return_value=0) as run:
            self.call(["view", "attach", "t1", "pm"])
        self.assertEqual(run.call_args.kwargs["poll"], 7.0)

    def test_view_needs_a_subcommand(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["view"])

    def test_open_cli_wires_ships_and_flags(self):
        with mock.patch.object(opener, "open_ships") as open_ships:
            code, _, _ = self.call(["view", "open", "t1", "--command", "yamato",
                                    "-o", "/tmp/x.kdl", "--session", "sess"])
        self.assertEqual(code, 0)
        open_ships.assert_called_once_with(["t1"], command="yamato", output="/tmp/x.kdl", session="sess")

    def test_open_cli_no_ships_and_no_session_uses_opener_defaults(self):
        with mock.patch.object(opener, "open_ships") as open_ships:
            self.call(["view", "open"])
        open_ships.assert_called_once_with(None, command=None, output=None)


# A `claude` that only knows what the view uses: `agents --json --all` reads a JSON file,
# `attach <id>` logs the id and stays in the foreground until it is killed. Each `agents`
# adds a line to $FAKE_VIEW_POLLS (the test counts the view's looks).
FAKE_VIEW_CLAUDE = """\
#!/usr/bin/env python3
import json, os, sys, time
if sys.argv[1:2] == ["agents"]:
    with open(os.environ["FAKE_VIEW_POLLS"], "a") as f:
        f.write("agents\\n")
    print(open(os.environ["FAKE_VIEW_AGENTS"]).read())
elif sys.argv[1:2] == ["attach"]:
    with open(os.environ["FAKE_VIEW_LOG"], "a") as f:
        f.write(sys.argv[2] + "\\n")
    time.sleep(60)
else:
    sys.exit(2)
"""


class AttachProcessTest(ShipTestCase):
    """`yamato view attach` as a real process, against a fake claude: only a live seat is attached."""

    def setUp(self):
        super().setUp()
        self.fake_bin = self.tmp / "claude-view"
        self.fake_bin.write_text(FAKE_VIEW_CLAUDE)
        self.fake_bin.chmod(0o755)
        self.agents = self.tmp / "agents.json"
        self.log = self.tmp / "attach.log"
        self.log.touch()
        self.polls = self.tmp / "polls.log"
        self.polls.touch()
        start_shift(self.shipdir, "pm", SID_A)

    def set_agents(self, pid):
        self.agents.write_text(json.dumps([{"sessionId": SID_A, "id": SID_A[:8], "pid": pid}]))

    def start(self):
        env = dict(os.environ, YAMATO_CLAUDE=str(self.fake_bin), FAKE_VIEW_AGENTS=str(self.agents),
                   FAKE_VIEW_LOG=str(self.log), FAKE_VIEW_POLLS=str(self.polls))
        return subprocess.Popen([sys.executable, str(YAMATO_BIN), "view", "attach", "t1", "pm",
                                 "--poll", "0.05"], env=env, stdout=subprocess.PIPE, text=True)

    def stop(self, proc):
        proc.send_signal(signal.SIGINT)
        out, _ = proc.communicate(timeout=15)
        return proc.returncode, out

    def wait_for(self, cond, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_attaches_to_the_rosters_live_session(self):
        self.set_agents(LIVE_PID)
        proc = self.start()
        try:
            self.assertTrue(self.wait_for(lambda: self.log.read_text().strip()), "never attached")
        finally:
            code, out = self.stop(proc)
        self.assertEqual(self.log.read_text().split(), [SID_A[:8]])   # the short id
        self.assertEqual(code, 130)
        self.assertIn("[t1.pm]", out)

    def test_never_attaches_to_a_stopped_session(self):
        self.set_agents(None)
        proc = self.start()
        try:
            self.assertTrue(self.wait_for(lambda: len(self.polls.read_text().split()) >= 3), "never polled")
            self.assertIsNone(proc.poll())
        finally:
            code, out = self.stop(proc)
        self.assertEqual(self.log.read_text(), "")
        self.assertEqual(code, 130)
        self.assertIn("待機中", out)


if __name__ == "__main__":
    unittest.main()
