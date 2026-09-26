"""Seat lifecycle against tests/fake_claude.py (no real Claude sessions)."""
import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import deadline, roster, runtime, seat
from yamato.util import YamatoError


class SeatTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(seat, "spawn_watchdog")
        p.start()
        self.addCleanup(p.stop)
        self.spawned = []
        p2 = mock.patch.object(seat, "_spawn_detached", side_effect=self.spawned.append)
        p2.start()
        self.addCleanup(p2.stop)

    def run_cmd(self, fn, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(*args)
        return buf.getvalue()

    def bg_calls(self):
        return [c for c in self.fake()["calls"] if "--bg" in c["argv"] and "--resume" not in c["argv"]]

    def resume_calls(self):
        return [c for c in self.fake()["calls"] if "--resume" in c["argv"]]

    def up(self, for_="20m"):
        return self.run_cmd(seat.up, self.shipdir, for_)

    def stop_session(self, name):
        st = self.fake()
        for s in st["sessions"]:
            if s["sessionId"] == roster.seat(self.shipdir, name)["sessionId"]:
                s["pid"] = None
        self.fake_state.write_text(json.dumps(st))

    # --- up ---

    def test_up_launches_hub_with_the_verified_recipe(self):
        os.environ["GH_TOKEN"] = "secret"
        self.addCleanup(os.environ.pop, "GH_TOKEN", None)
        out = self.up()
        [call] = self.bg_calls()
        a = call["argv"]
        self.assertEqual(call["cwd"], str(self.workspace))
        self.assertIsNone(call["GH_TOKEN"])
        self.assertEqual(a[a.index("--name") + 1], "t1.pm")
        self.assertEqual(a[a.index("--agent") + 1], "pm")
        self.assertEqual(a[a.index("--model") + 1], "opus")
        self.assertEqual(a[a.index("--setting-sources") + 1], "project,local")
        self.assertEqual(a[a.index("--settings") + 1], str(runtime.settings_path(self.shipdir, "pm")))
        self.assertEqual(a[a.index("--add-dir") + 1], str(self.shipdir))
        self.assertEqual(a.count("--settings"), 1)
        self.assertEqual(a[-2], "--")
        self.assertIn("pm", json.loads(a[a.index("--agents") + 1]))
        rec = roster.seat(self.shipdir, "pm")
        self.assertEqual(rec["sessionId"], self.fake()["sessions"][0]["sessionId"])
        self.assertEqual(len(rec["sessionId"]), 36)
        dl = deadline.read(self.shipdir)
        self.assertAlmostEqual(dl["deadline"] - dl["upAt"], 1200, delta=1)
        self.assertIn("新しいシフトを起動した", out)
        self.assertIsNone(roster.seat(self.shipdir, "impl").get("sessionId"))  # only the hub

    def test_up_twice_does_not_start_a_second_hub(self):
        self.up()
        out = self.up()
        self.assertEqual(len(self.bg_calls()), 1)
        self.assertIn("すでに動いている", out)

    def test_up_refuses_untrusted_workspace(self):
        (self.config / ".claude.json").write_text(json.dumps({"projects": {}}))
        with self.assertRaises(YamatoError) as cm:
            self.up()
        self.assertIn("trust", str(cm.exception))
        self.assertEqual(self.bg_calls(), [])

    def test_untrusted_message_from_claude_is_explained(self):
        self.set_fake_mode(untrusted=True)
        with self.assertRaises(YamatoError) as cm:
            self.up()
        self.assertIn("trust", str(cm.exception))

    def test_git_workspace_needs_trust_on_the_git_root(self):
        import subprocess

        sub = self.workspace / "repo"
        sub.mkdir()
        subprocess.run(["git", "init", "-q", str(sub)], check=True)
        from yamato import claude

        self.assertFalse(claude.is_trusted(sub))  # the trusted parent does not cover a git root
        self.assertTrue(claude.is_trusted(self.workspace))

    # --- send ---

    def test_send_before_up_only_records(self):
        out = self.run_cmd(seat.send, self.shipdir, "pm", "hello", "owner")
        self.assertIn("起動していない", out)
        self.assertEqual(self.fake()["calls"], [])

    def test_send_to_live_seat_does_not_wake_it(self):
        self.up()
        out = self.run_cmd(seat.send, self.shipdir, "pm", "report", "impl")
        self.assertEqual(len(self.bg_calls()), 1)
        self.assertEqual(self.resume_calls(), [])
        self.assertIn('SendMessage ツールで to="t1.pm"', out)
        self.assertIn("[yamato inbox #1 from impl] report", out)

    def test_send_to_stopped_persistent_resumes_with_full_id(self):
        self.up()
        sid = roster.seat(self.shipdir, "pm")["sessionId"]
        self.stop_session("pm")
        out = self.run_cmd(seat.send, self.shipdir, "pm", "report", "impl")
        [call] = self.resume_calls()
        self.assertEqual(call["argv"][call["argv"].index("--resume") + 1], sid)
        self.assertEqual(call["argv"][-2], "--")
        rec = roster.seat(self.shipdir, "pm")
        self.assertEqual((rec["sessionId"], rec["shiftNo"], rec["how"]), (sid, 2, "resume"))
        self.assertIn("resume した", out)
        self.assertEqual(len(self.bg_calls()), 1)

    def test_resume_that_starts_a_copy_fails_and_discards_the_copy(self):
        self.up()
        self.stop_session("pm")
        self.set_fake_mode(copy=True)
        with self.assertRaises(YamatoError) as cm:
            self.run_cmd(seat.send, self.shipdir, "pm", "x", "impl")
        self.assertIn("コピー", str(cm.exception))
        self.assertEqual([s["name"] for s in self.fake()["sessions"]], ["t1.pm"])  # copy stopped + rm'd
        argvs = [c["argv"][0] for c in self.fake()["calls"]]
        self.assertIn("stop", argvs)
        self.assertIn("rm", argvs)

    def test_send_to_per_task_starts_a_new_shift_every_time(self):
        self.up()
        self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm")
        first = roster.seat(self.shipdir, "impl")["sessionId"]
        self.stop_session("impl")
        self.run_cmd(seat.send, self.shipdir, "impl", "T-002", "pm")
        second = roster.seat(self.shipdir, "impl")
        self.assertNotEqual(first, second["sessionId"])
        self.assertEqual(second["shiftNo"], 2)
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual(len(self.bg_calls()), 3)

    def test_send_past_deadline_does_not_wake_and_tells_sender_to_wrap_up(self):
        self.up()
        deadline.end_now(self.shipdir)
        out = self.run_cmd(seat.send, self.shipdir, "impl", "more work", "pm")
        self.assertEqual(len(self.bg_calls()), 1)
        self.assertIn("上限を過ぎている", out)
        self.assertIn(f"seat-stop {self.shipdir} pm", out)

    # --- ending shifts ---

    def test_enforce_past_grace_stops_and_records_no_handoff(self):
        self.up()
        self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm")
        d = deadline.read(self.shipdir)
        d["deadline"] = d["graceUntil"] = time.time() - 1
        deadline.write_raw(self.shipdir, d)
        stopped = seat.enforce(self.shipdir, self.team())
        self.assertEqual(sorted(stopped), ["impl", "pm"])
        for s in ("pm", "impl"):
            rec = roster.seat(self.shipdir, s)
            self.assertEqual((rec["state"], rec["endReason"], rec["note"]),
                             (roster.OFF, "grace-exceeded", "引き継ぎなしで終了"))
        self.assertTrue(all(s["pid"] is None for s in self.fake()["sessions"]))
        usage_lines = (self.shipdir / "usage.jsonl").read_text().splitlines()
        self.assertEqual(len(usage_lines), 2)

    def test_down_force(self):
        self.up()
        out = self.run_cmd(seat.down, self.shipdir, True)
        self.assertIn("pm", out)
        self.assertEqual(roster.seat(self.shipdir, "pm")["endReason"], "down-force")
        self.assertEqual(deadline.phase(deadline.read(self.shipdir)), deadline.FORCE)

    def test_down_starts_the_grace_period(self):
        self.up()
        out = self.run_cmd(seat.down, self.shipdir, False)
        self.assertEqual(deadline.phase(deadline.read(self.shipdir)), deadline.OVER)
        self.assertIn("終業を指示した", out)
        self.assertIsNotNone(self.fake()["sessions"][0]["pid"])  # not stopped: the hooks wrap it up

    def test_seat_stop_checks_session_and_handoff_then_delays_stop(self):
        self.up()
        rec = roster.seat(self.shipdir, "pm")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            with self.assertRaises(YamatoError):
                seat.seat_stop(self.shipdir, "pm", 10)
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "someone-else"}):
            with self.assertRaises(YamatoError):
                seat.seat_stop(self.shipdir, "pm", 10)
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": rec["sessionId"]}):
            with self.assertRaises(YamatoError) as cm:
                seat.seat_stop(self.shipdir, "pm", 10)
            self.assertIn("handoff.md", str(cm.exception))
            (self.shipdir / "seats/pm/handoff.md").write_text("# pm 引き継ぎ\n- 次: なし\n")
            out = self.run_cmd(seat.seat_stop, self.shipdir, "pm", 7)
        self.assertIn("7 秒後", out)
        self.assertEqual(roster.seat(self.shipdir, "pm")["state"], roster.STOPPING)
        [args] = self.spawned
        self.assertEqual(args[:2], ["sh", "-c"])
        self.assertIn(f"sleep 7; ", args[2])
        self.assertIn(f" stop {rec['sessionId'][:8]}; ", args[2])
        self.assertIn("_shift-ended", args[2])
        # the delayed part: after the stop, the shift is closed with its usage
        self.stop_session("pm")
        seat.shift_ended(self.shipdir, "pm", rec["sessionId"])
        rec = roster.seat(self.shipdir, "pm")
        self.assertEqual((rec["state"], rec["endReason"], rec["handoffWritten"], rec["note"]),
                         (roster.OFF, "seat-stop", True, None))
        self.assertTrue((self.shipdir / "usage.jsonl").is_file())

    def test_reconcile_closes_shifts_whose_process_vanished(self):
        self.up()
        self.stop_session("pm")
        self.run_cmd(seat.status, self.shipdir)
        rec = roster.seat(self.shipdir, "pm")
        self.assertEqual((rec["state"], rec["endReason"], rec["note"]), (roster.OFF, "exited", "引き継ぎなしで終了"))

    def test_status_flags_permission_prompt(self):
        self.set_fake_mode(waitingFor="permission prompt")
        self.up()
        out = self.run_cmd(seat.status, self.shipdir)
        self.assertIn("生存=yes", out)
        self.assertIn("!!! 詰まり: pm", out)
        self.assertIn("残り", out)


if __name__ == "__main__":
    unittest.main()
