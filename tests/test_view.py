import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from yamato.view import attach, layout, shipfiles  # noqa: E402
from yamato.view.shipfiles import ViewError  # noqa: E402

SID_A = "aaaaaaaa-0000-4000-8000-000000000001"
SID_B = "bbbbbbbb-0000-4000-8000-000000000002"
LIVE_PID = os.getpid()


def write_roster(shipdir: Path, seats: dict) -> None:
    (shipdir / "roster.json").write_text(json.dumps({"seats": seats, "shifts": []}))


class ShipDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class RosterTest(ShipDirTest):
    def test_no_roster(self):
        self.assertIsNone(shipfiles.roster_session_id(self.dir, "pm"))

    def test_broken_roster(self):
        (self.dir / "roster.json").write_text("{not json")
        self.assertIsNone(shipfiles.roster_session_id(self.dir, "pm"))

    def test_seat_missing_or_without_session(self):
        write_roster(self.dir, {"impl": {"sessionId": SID_A}, "qa": {"state": "off"}})
        self.assertIsNone(shipfiles.roster_session_id(self.dir, "pm"))
        self.assertIsNone(shipfiles.roster_session_id(self.dir, "qa"))
        self.assertEqual(shipfiles.roster_session_id(self.dir, "impl"), SID_A)


class ResolverTest(ShipDirTest):
    def resolver(self, listing, seat="pm"):
        calls = []

        def agents():
            calls.append(1)
            return listing
        return attach.roster_resolver(self.dir, seat, agents=agents), calls

    def test_live_session(self):
        write_roster(self.dir, {"pm": {"sessionId": SID_A, "state": "on_shift"}})
        r, _ = self.resolver([{"sessionId": SID_A, "id": SID_A[:8], "pid": LIVE_PID}])
        # attach takes the short id; the full sessionId gives "No job matching"
        self.assertEqual(r(), SID_A[:8])

    def test_short_id_falls_back_to_prefix(self):
        write_roster(self.dir, {"pm": {"sessionId": SID_A}})
        r, _ = self.resolver([{"sessionId": SID_A, "pid": LIVE_PID}])
        self.assertEqual(r(), SID_A[:8])

    def test_stopped_session_is_not_attached(self):
        # pid null = stopped; attaching would resurrect it (spike Q4)
        write_roster(self.dir, {"pm": {"sessionId": SID_A, "state": "on_shift"}})
        r, _ = self.resolver([{"sessionId": SID_A, "pid": None}])
        self.assertIsNone(r())

    def test_dead_pid_is_not_attached(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        write_roster(self.dir, {"pm": {"sessionId": SID_A}})
        r, _ = self.resolver([{"sessionId": SID_A, "pid": dead.pid}])
        self.assertIsNone(r())

    def test_session_unknown_to_claude(self):
        write_roster(self.dir, {"pm": {"sessionId": SID_A}})
        r, _ = self.resolver([{"sessionId": SID_B, "pid": LIVE_PID}])
        self.assertIsNone(r())

    def test_only_roster_decides_not_the_name(self):
        # another live session with the seat's name is not picked (--name is not unique)
        write_roster(self.dir, {"pm": {"sessionId": SID_A}})
        r, _ = self.resolver([
            {"sessionId": SID_A, "name": "dev.pm", "pid": None},
            {"sessionId": SID_B, "name": "dev.pm", "pid": LIVE_PID},
        ])
        self.assertIsNone(r())

    def test_no_roster_does_not_ask_claude(self):
        r, calls = self.resolver([{"sessionId": SID_A, "pid": LIVE_PID}])
        self.assertIsNone(r())
        self.assertEqual(calls, [])

    def test_follows_roster_changes(self):
        listing = [{"sessionId": SID_A, "id": "aaaaaaaa", "pid": LIVE_PID},
                   {"sessionId": SID_B, "id": "bbbbbbbb", "pid": LIVE_PID}]
        r, _ = self.resolver(listing)
        write_roster(self.dir, {"pm": {"sessionId": SID_A}})
        self.assertEqual(r(), "aaaaaaaa")
        write_roster(self.dir, {"pm": {"sessionId": SID_B}})
        self.assertEqual(r(), "bbbbbbbb")


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
        code, procs, out = self.run_loop([ViewError("boom"), None], rounds=2)
        self.assertEqual(code, 0)
        self.assertEqual(procs, [])
        self.assertIn("boom", out)


TEAM_YAML = """\
# comment
name: dev
hub: pm
workspace: "/tmp/ws"   # trailing comment

roles:
  pm:
    model: opus
    shift: persistent
    description: captain # count: 9 in a comment is ignored
  impl:
    model: sonnet
    count: 2                 # impl-1, impl-2
  reviewer: { model: opus, shift: per_task }
  qa: { model: sonnet, count: 3 }

board:
  kinds: [task]
"""


class TeamSeatsTest(ShipDirTest):
    def test_from_team_yaml(self):
        (self.dir / "team.yaml").write_text(TEAM_YAML)
        self.assertEqual(shipfiles.team_seats(self.dir),
                         ["pm", "impl-1", "impl-2", "reviewer", "qa-1", "qa-2", "qa-3"])

    def test_runtime_team_json_wins(self):
        (self.dir / "team.yaml").write_text(TEAM_YAML)
        (self.dir / ".runtime").mkdir()
        (self.dir / ".runtime" / "team.json").write_text(json.dumps(
            {"seats": {"pm": {"role": "pm"}, "impl": {"role": "impl"}}}))
        self.assertEqual(shipfiles.team_seats(self.dir), ["pm", "impl"])

    def test_missing_team_yaml(self):
        with self.assertRaises(ViewError):
            shipfiles.team_seats(self.dir)

    def test_no_roles(self):
        (self.dir / "team.yaml").write_text("name: dev\nhub: pm\n")
        with self.assertRaises(ViewError):
            shipfiles.team_seats(self.dir)


class LayoutTest(unittest.TestCase):
    def test_tabs_and_panes(self):
        kdl = layout.build([("dev", "dev", ["pm", "impl-1", "reviewer"]),
                            ("research", "/x/research", ["editor"])], "/r/bin/yamato-seat-attach")
        self.assertEqual(kdl.count("tab name="), 2)
        self.assertIn('tab name="dev" focus=true {', kdl)
        self.assertIn('tab name="research" {', kdl)
        self.assertEqual(kdl.count('command="/r/bin/yamato-seat-attach"'), 4)
        self.assertIn('args "dev" "impl-1"', kdl)
        self.assertIn('args "/x/research" "editor"', kdl)
        # two seats per row: pm|impl-1 share a vertical split, reviewer is alone
        self.assertEqual(kdl.count('split_direction="vertical"'), 1)
        self.assertEqual(kdl.count("{"), kdl.count("}"))

    def test_quoting(self):
        self.assertEqual(layout.kdl_str('a"b\\c'), '"a\\"b\\\\c"')

    def test_layout_for_reads_team_yaml(self):
        with tempfile.TemporaryDirectory() as home:
            ship = Path(home) / "dev"
            ship.mkdir()
            (ship / "team.yaml").write_text(TEAM_YAML)
            old = os.environ.get("YAMATO_HOME")
            os.environ["YAMATO_HOME"] = home
            try:
                by_name = layout.layout_for(["dev"], "seat-attach")
                by_path = layout.layout_for([str(ship)], "seat-attach")
            finally:
                if old is None:
                    del os.environ["YAMATO_HOME"]
                else:
                    os.environ["YAMATO_HOME"] = old
        self.assertEqual(by_name.count("pane name="), 7)
        self.assertIn('args "dev" "qa-3"', by_name)
        self.assertIn(f'args "{ship.resolve()}" "qa-3"', by_path)

    def test_default_command_is_repo_bin(self):
        self.assertTrue(layout.SEAT_ATTACH.is_file())


if __name__ == "__main__":
    unittest.main()
