"""Seat lifecycle against tests/fake_claude.py (no real Claude sessions)."""
import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import deadline, events, inbox, roster, runtime, seat
from yamato.util import YamatoError


class _SeatBase(ShipTestCase):
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


class SeatTest(_SeatBase):
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

    def test_launch_adopts_the_session_when_the_output_has_no_id(self):
        self.set_fake_mode(noid=True)
        self.up()
        rec = roster.seat(self.shipdir, "pm")
        self.assertEqual(rec["sessionId"], self.fake()["sessions"][0]["sessionId"])

    def test_env_unset_comes_from_team_yaml(self):
        os.environ["GH_TOKEN"] = "secret"
        self.addCleanup(os.environ.pop, "GH_TOKEN", None)
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("env_unset: [GH_TOKEN, GITHUB_TOKEN]", "env_unset: []"))
        os.environ["CLAUDE_CODE_SESSION_ID"] = "caller"
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_SESSION_ID", None)
        self.up()
        [call] = self.bg_calls()
        self.assertEqual(call["GH_TOKEN"], "secret")
        self.assertIsNone(call["CLAUDE_CODE_SESSION_ID"])  # the caller's identity never leaks

    def test_up_resumes_a_stopped_hub_with_an_up_prompt(self):
        self.up()
        self.stop_session("pm")
        out = self.up()
        [call] = self.resume_calls()
        self.assertIn("艦が起動された", call["argv"][-1])
        self.assertIn("resume した", out)

    def test_send_to_owner_records_and_notifies(self):
        marker = self.tmp / "notified"
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("  via: []", f'  via: [command, slack]\n  command: "echo $YAMATO_MESSAGE > {marker}"'))
        self.up()
        with mock.patch.dict(os.environ):
            os.environ.pop("YAMATO_SLACK_WEBHOOK", None)   # never reach a real webhook from a test
            out = self.run_cmd(seat.send, self.shipdir, "owner", "判断ください", "pm")
        self.assertIn("owner の inbox に記録した", out)
        self.assertIn("通知 command: exit 0", out)
        self.assertIn("通知 slack: 失敗 (環境変数 YAMATO_SLACK_WEBHOOK が空)", out)   # the command still went out
        [failed] = events.read(self.shipdir, kinds="notify_failed")
        self.assertEqual(failed["data"]["via"], "slack")
        self.assertEqual(marker.read_text().strip(), "判断ください")
        from yamato import inbox
        self.assertEqual(inbox.unread(self.shipdir, "owner")[0]["from"], "pm")
        self.assertTrue((self.shipdir / "owner" / "inbox.jsonl").is_file())

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

    def test_trust_unknown_from_claude_json(self):
        from yamato import claude

        cfg = self.config / ".claude.json"
        for text in ("{broken", "[]", json.dumps({"projects": []}), json.dumps({"other": {}})):
            cfg.write_text(text)
            self.assertIsNone(claude.is_trusted(self.workspace), text)
        cfg.unlink()
        self.assertIsNone(claude.is_trusted(self.workspace))
        from yamato import ship

        _, warnings = ship.create("t9", str(self.workspace), None, "dev")
        self.assertTrue([w for w in warnings if "確かめられなかった" in w])
        cfg.write_text(json.dumps({"projects": {str(self.workspace): "odd"}}))
        self.assertFalse(claude.is_trusted(self.workspace))

    def test_up_with_unknown_trust_warns_and_launches(self):
        (self.config / ".claude.json").unlink()
        out = self.up()
        self.assertIn("確かめられなかった", out)
        self.assertEqual(len(self.bg_calls()), 1)

    def test_up_with_unknown_trust_stops_on_claudes_answer(self):
        (self.config / ".claude.json").write_text("{broken")
        self.set_fake_mode(untrusted=True)
        with self.assertRaises(YamatoError) as cm:
            self.up()
        self.assertIn("trust されていません", str(cm.exception))

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

    def test_send_from_omitted_uses_the_calling_seat(self):
        """#7: --from を省くと呼び出し元のセッション (roster.seat_of_session) を送り手にする。"""
        sid = "c" * 36
        roster.start_shift(self.shipdir, "impl", session_id=sid, short_id=sid[:8], session_name="t1.impl", how="new")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": sid}):
            self.run_cmd(seat.send, self.shipdir, "pm", "hello", None)
        [entry] = inbox.entries(self.shipdir, "pm")
        self.assertEqual(entry["from"], "impl")

    def test_send_from_omitted_and_caller_unknown_defaults_to_owner(self):
        """呼び出し元が席のセッションでなければ、今までどおり owner になる。"""
        self.run_cmd(seat.send, self.shipdir, "pm", "hello", None)
        [entry] = inbox.entries(self.shipdir, "pm")
        self.assertEqual(entry["from"], "owner")

    def test_send_from_explicit_mismatch_warns_but_keeps_the_explicit_sender(self):
        """--from と呼び出し元が食い違えば警告するだけで、記録は --from を使う (拒否しない)。"""
        sid = "d" * 36
        roster.start_shift(self.shipdir, "impl", session_id=sid, short_id=sid[:8], session_name="t1.impl", how="new")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": sid}):
            out = self.run_cmd(seat.send, self.shipdir, "pm", "hello", "owner")
        self.assertIn("食い違う", out)
        [entry] = inbox.entries(self.shipdir, "pm")
        self.assertEqual(entry["from"], "owner")

    def test_concurrent_sends_to_a_stopped_seat_launch_it_once(self):
        """Review B1: two senders racing must not start the seat twice."""
        import threading

        self.up()
        self.set_fake_mode(slow_launch=0.2)
        errors = []

        def send(text):
            try:
                seat.send(self.shipdir, "impl", text, "pm")
            except Exception as e:  # pragma: no cover - surfaced below
                errors.append(e)

        with mock.patch.object(seat, "out"):
            threads = [threading.Thread(target=send, args=(f"msg{i}",)) for i in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(errors, [])
        impl_launches = [c for c in self.bg_calls() if c["argv"][c["argv"].index("--name") + 1] == "t1.impl"]
        self.assertEqual(len(impl_launches), 1)
        impl_sessions = [x for x in self.fake()["sessions"] if x["name"] == "t1.impl"]
        self.assertEqual(roster.seat(self.shipdir, "impl")["sessionId"], impl_sessions[0]["sessionId"])
        from yamato import inbox
        self.assertEqual([e["n"] for e in inbox.entries(self.shipdir, "impl")], [1, 2, 3])

    def test_resume_runs_in_the_workspace(self):
        self.up()
        self.stop_session("pm")
        self.run_cmd(seat.send, self.shipdir, "pm", "x", "owner")
        [call] = self.resume_calls()
        self.assertEqual(call["cwd"], str(self.workspace))

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

    # --- a launch that did not come up (verify-p0-c Q5) ---

    def short_launch_check(self):
        for name, value in (("LAUNCH_SETTLE", 0.02), ("LAUNCH_CHECK_TIMEOUT", 0.05), ("LAUNCH_CHECK_POLL", 0.01)):
            p = mock.patch.object(seat.claude, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_a_worker_that_died_before_init_fails_the_send(self):
        self.short_launch_check()
        self.up()
        self.set_fake_mode(session={"pid": None, "state": "failed", "detail": "exit 1 before init"})
        with self.assertRaises(YamatoError) as cm:
            self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm")
        msg = str(cm.exception)
        self.assertIn("席 impl の起動に失敗しました (new", msg)
        self.assertIn("state: failed, pid なし): exit 1 before init", msg)
        self.assertIn("inbox に記録済み (impl #1)", msg)
        rec = roster.seat(self.shipdir, "impl")
        self.assertIsNone(rec.get("state"))   # no shift was started
        self.assertEqual(rec["launchFailed"]["how"], "new")
        [ev] = events.read(self.shipdir, kinds=events.LAUNCH_FAILED)
        self.assertEqual(ev["seat"], "impl")
        self.assertIn("exit 1 before init", ev["data"]["reason"])
        self.assertIn("[起動失敗", self.run_cmd(seat.status, self.shipdir))
        # the next launch that comes up clears it
        self.set_fake_mode()
        self.run_cmd(seat.send, self.shipdir, "impl", "again", "pm")
        self.assertIsNone(roster.seat(self.shipdir, "impl")["launchFailed"])

    def test_a_live_session_in_state_failed_is_stopped_and_fails(self):
        self.short_launch_check()
        self.set_fake_mode(session={"state": "failed"})   # a bad --model: the process lives on
        with self.assertRaises(YamatoError) as cm:
            self.up()
        self.assertIn("エラーで止まっている (state: failed)", str(cm.exception))
        self.assertIsNone(self.fake()["sessions"][0]["pid"])   # stopped: it would escape the time limit
        self.assertIn("stop", [c["argv"][0] for c in self.fake()["calls"]])

    def test_a_resume_that_did_not_come_up_fails(self):
        self.short_launch_check()
        self.up()
        self.stop_session("pm")
        self.set_fake_mode(session={"pid": None, "state": "failed"})
        with self.assertRaises(YamatoError) as cm:
            self.run_cmd(seat.send, self.shipdir, "pm", "x", "impl")
        self.assertIn("(resume", str(cm.exception))
        rec = roster.seat(self.shipdir, "pm")
        self.assertEqual((rec["shiftNo"], rec["launchFailed"]["how"]), (1, "resume"))

    def test_a_resume_is_not_judged_by_the_previous_shifts_failed_record(self):
        # the listing keeps the last shift's ``state: failed`` (no pid) until the resumed
        # worker gets its pid: that must not count as this resume failing
        self.short_launch_check()
        for name, value in (("LAUNCH_SETTLE", 0), ("LAUNCH_CHECK_TIMEOUT", 1.0)):
            p = mock.patch.object(seat.claude, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.up()
        st = self.fake()
        st["sessions"][0].update(pid=None, state="failed", detail="API error")
        st["mode"] = {"resume_lag": 3, "session": {"state": "working", "detail": None}}
        self.fake_state.write_text(json.dumps(st))
        self.run_cmd(seat.send, self.shipdir, "pm", "x", "impl")
        rec = roster.seat(self.shipdir, "pm")
        self.assertEqual((rec["shiftNo"], rec["how"], rec.get("launchFailed")), (2, "resume", None))
        self.assertEqual(events.read(self.shipdir, kinds=events.LAUNCH_FAILED), [])

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

    def test_forced_stop_by_the_seats_own_hooks(self):
        """§0 B4: past the grace period with the watchdog gone, a seat hook stops the seat."""
        from yamato import report

        self.up()
        rec = roster.seat(self.shipdir, "pm")
        seat.spawn_delayed_stop(self.shipdir, "pm", rec["sessionId"], 5, forced=True)
        [args] = self.spawned
        self.assertIn(f"sleep 5; ", args[2])
        self.assertIn(f" stop {rec['sessionId'][:8]}; ", args[2])
        self.assertTrue(args[2].endswith(f"_shift-ended {self.shipdir} pm {rec['sessionId']} --forced"))
        self.stop_session("pm")
        with mock.patch.object(report, "safety_net") as net:
            seat.shift_ended(self.shipdir, "pm", rec["sessionId"], forced=True)
            seat.shift_ended(self.shipdir, "pm", rec["sessionId"], forced=True)   # once
        net.assert_called_once()
        rec = roster.seat(self.shipdir, "pm")
        self.assertEqual((rec["state"], rec["endReason"], rec["note"]),
                         (roster.OFF, "grace-exceeded", roster.NO_HANDOFF_NOTE))
        stops = [e for e in events.read(self.shipdir) if e["kind"] == events.FORCE_STOP]
        self.assertEqual([e["data"]["by"] for e in stops], ["hook"])

    def test_seat_stop_refuses_while_a_live_recipient_has_not_read_the_report(self):
        self.up()
        self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm")
        impl = roster.seat(self.shipdir, "impl")
        (self.shipdir / "seats/impl/handoff.md").write_text("# impl\n")
        self.run_cmd(seat.send, self.shipdir, "pm", "T-001 done", "impl")  # pm alive: SendMessage owed
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": impl["sessionId"]}):
            with self.assertRaises(YamatoError) as cm:
                seat.seat_stop(self.shipdir, "impl", 10)
            self.assertIn('to="t1.pm" inbox #1', str(cm.exception))
            # once pm has read its inbox, the debt is settled
            from yamato import inbox
            inbox.mark_read(self.shipdir, "pm", 1)
            self.run_cmd(seat.seat_stop, self.shipdir, "impl", 10)
        self.assertEqual(roster.seat(self.shipdir, "impl")["state"], roster.STOPPING)

    def test_seat_stop_checks_can_be_turned_off_in_team_yaml(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("require_handoff: true", "require_handoff: false")
                      .replace("require_delivery: true", "require_delivery: false"))
        self.up()
        self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm")
        self.run_cmd(seat.send, self.shipdir, "pm", "done", "impl")
        impl = roster.seat(self.shipdir, "impl")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": impl["sessionId"]}):
            self.run_cmd(seat.seat_stop, self.shipdir, "impl", 10)  # no handoff, report unread
        rec = roster.seat(self.shipdir, "impl")
        self.assertEqual((rec["state"], rec["handoffWritten"]), (roster.STOPPING, False))

    def test_seat_stop_delivered_flag(self):
        self.up()
        self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm")
        impl = roster.seat(self.shipdir, "impl")
        (self.shipdir / "seats/impl/handoff.md").write_text("# impl\n")
        self.run_cmd(seat.send, self.shipdir, "pm", "T-001 done", "impl")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": impl["sessionId"]}):
            self.run_cmd(seat.seat_stop, self.shipdir, "impl", 10, True)
        self.assertEqual(roster.seat(self.shipdir, "impl")["state"], roster.STOPPING)
        # a new shift starts with a clean slate
        self.stop_session("impl")
        self.run_cmd(seat.send, self.shipdir, "impl", "T-002", "pm")
        self.assertEqual(seat.unresolved_pending(self.shipdir, "impl"), [])

    def test_send_to_a_seat_in_its_stop_delay_waits_and_starts_a_new_shift(self):
        self.up()
        self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm")
        first = roster.seat(self.shipdir, "impl")["sessionId"]
        roster.mark_stopping(self.shipdir, "impl", handoff_written=True)
        waited = []

        def fake_wait(sid, timeout=30):
            waited.append(sid)
            self.stop_session("impl")  # the delayed stop lands
            return True

        with mock.patch("yamato.claude.wait_gone", side_effect=fake_wait):
            out = self.run_cmd(seat.send, self.shipdir, "impl", "差し戻し", "pm")
        self.assertEqual(waited[0], first)
        rec = roster.seat(self.shipdir, "impl")
        self.assertNotEqual(rec["sessionId"], first)
        self.assertEqual(rec["shiftNo"], 2)
        self.assertIn("新しいシフトを起動した", out)
        self.assertNotIn("SendMessage ツールで", out)

    def test_long_handoff_warns_with_the_inject_limit_but_does_not_fail(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("handoff: [40, 2000]", "handoff: [5, 2000]"))
        self.up()
        rec = roster.seat(self.shipdir, "pm")
        (self.shipdir / "seats/pm/handoff.md").write_text("\n".join(f"l{i}" for i in range(8)))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": rec["sessionId"]}):
            out = self.run_cmd(seat.seat_stop, self.shipdir, "pm", 10)
        self.assertIn("8 行ある (注入の上限 5 行)", out)
        self.assertEqual(roster.seat(self.shipdir, "pm")["state"], roster.STOPPING)

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
        self.assertIn("!!! pm: 詰まり", out)
        self.assertIn("残り", out)


class RealClaudeProcessTest(_SeatBase):
    """The fake claude as a real process each time, as ``$YAMATO_CLAUDE`` is run in production:
    the other tests run it in-process (helpers.patch_fast). The recipe (argv, cwd, env) and
    resume in the workspace."""
    real_claude_process = True

    test_up_launches_hub_with_the_verified_recipe = SeatTest.test_up_launches_hub_with_the_verified_recipe
    test_resume_runs_in_the_workspace = SeatTest.test_resume_runs_in_the_workspace


if __name__ == "__main__":
    unittest.main()
