"""shift: headless and run-headless (design-p1 §4) against tests/fake_claude.py -p."""
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.fake_claude_lib.print_mode import RESULT_TEXT
from tests.helpers import ShipTestCase
from yamato import board, deadline, events, headless, inbox, procs, roster, runtime, seat
from yamato.team import validate
from yamato.util import YamatoError

ROLE = """roles:
  researcher:
    model: sonnet
    shift: headless
    description: 調査担当
"""


def _reap(proc):
    if proc.poll() is None:
        proc.kill()
    proc.wait()


class _Base(ShipTestCase):
    role_extra = ""
    extra_roles = ()

    def setUp(self):
        super().setUp()
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text(encoding="utf-8").replace("roles:\n", ROLE + self.role_extra, 1), encoding="utf-8")
        for role in ("researcher", *self.extra_roles):
            (self.shipdir / "roles" / f"{role}.md").write_text(f"あなたは {role} です。\n", encoding="utf-8")
        seat.prepare(self.shipdir)
        deadline.write(self.shipdir, limit=600, grace=60, token="t")
        for target, attr, value in ((headless, "POLL", 0.02), (headless, "IDLE_POLL", 0.02), (headless, "KILL_WAIT", 5)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        # the report to the hub would launch it (fake --bg): keep the fake calls about -p only
        p = mock.patch.object(seat, "wake", return_value=("alive", {}))
        self.hub_wake = p.start()
        self.addCleanup(p.stop)

    def p_calls(self):
        return [c for c in self.fake()["calls"] if "-p" in c["argv"]]

    def run_wrapper(self, **mode):
        if mode:
            self.set_fake_mode(**mode)
        return headless.run(self.shipdir, "researcher")

    def usage_lines(self):
        path = self.shipdir / "usage.jsonl"
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def hub_inbox(self):
        return inbox.entries(self.shipdir, "pm")


class HeadlessTest(_Base):
    # --- team.yaml ---

    def test_team_yaml_headless_options(self):
        d = {"name": "dev", "hub": "pm", "workspace": "/tmp",
             "roles": {"pm": {"model": "opus", "shift": "persistent"},
                       "fc": {"shift": "headless", "max_duration": "30m", "max_budget_usd": 0.5,
                              "report_to": "owner"}}}
        t = validate(d, Path("/ship"))
        self.assertEqual(t["seats"]["fc"]["shift"], "headless")
        self.assertEqual(t["roles"]["fc"]["max_duration"], 1800)
        self.assertEqual(t["roles"]["fc"]["max_budget_usd"], 0.5)
        self.assertEqual(t["roles"]["fc"]["report_to"], "owner")
        self.assertIsNone(t["roles"]["pm"]["max_budget_usd"])
        d["roles"]["pm"]["max_duration"] = "1h"
        d["roles"]["pm"]["max_budget_usd"] = 2
        warnings = validate(d, Path("/ship"))["warnings"]
        self.assertTrue(any("roles.pm.max_duration" in w and "headless" in w for w in warnings))
        self.assertTrue(any("roles.pm.max_budget_usd" in w for w in warnings))
        self.assertFalse(any("roles.fc." in w for w in warnings))
        for bad in ({"report_to": "nobody"}, {"max_budget_usd": 0}, {"max_budget_usd": "1"},
                    {"max_duration": "0m"}):
            d["roles"]["fc"] = {"shift": "headless", **bad}
            with self.assertRaises(YamatoError):
                validate(d, Path("/ship"))

    # --- the recipe ---

    def test_runs_the_verified_p_recipe_with_the_session_id_in_the_roster_first(self):
        for k in ("GH_TOKEN", "CLAUDE_CODE_CHILD_SESSION"):
            os.environ[k] = "x"
            self.addCleanup(os.environ.pop, k, None)
        self.assertEqual(self.run_wrapper(p_seat_stop=True), 0)
        [call] = self.p_calls()
        a = call["argv"]
        self.assertEqual(call["cwd"], str(self.workspace))
        self.assertEqual(call["GH_TOKEN"], "x")  # D-003: env_unset の既定は空。gh の権限はトークン側で絞る
        self.assertIsNone(call["CLAUDE_CODE_CHILD_SESSION"])  # the caller's markers (transcript saving)
        self.assertTrue(call["stdin_is_devnull"])            # < /dev/null (V1)
        self.assertNotIn("--bare", a)                        # bare = Not logged in (V1)
        self.assertEqual(a[a.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", a)
        self.assertEqual(a[a.index("--permission-prompts") + 1], "none")
        self.assertEqual(a[a.index("--name") + 1], "t1.researcher")
        self.assertEqual(a[a.index("--agent") + 1], "researcher")
        self.assertEqual(a[a.index("--setting-sources") + 1], "project,local")
        self.assertEqual(a[a.index("--settings") + 1], str(runtime.settings_path(self.shipdir, "researcher")))
        self.assertEqual(a[a.index("--add-dir") + 1], str(self.shipdir))
        self.assertEqual(a[-2], "--")
        self.assertIn("seat-stop", a[-1])
        self.assertNotIn("--max-budget-usd", a)             # only when written (§4.4)
        sid = a[a.index("--session-id") + 1]
        shifts = roster.load(self.shipdir)["shifts"]
        self.assertEqual(shifts[-1]["sessionId"], sid)
        self.assertEqual(shifts[-1]["how"], "headless")

    def test_a_proper_shift_records_usage_rate_limit_and_reports_in_fixed_form(self):
        brd = board.Board(self.shipdir, self.team())
        item = brd.add("調べる", {"assignee": "researcher", "state": "active"})
        self.run_wrapper(p_seat_stop=True)
        rec = roster.seat(self.shipdir, "researcher")
        self.assertEqual(rec["state"], roster.OFF)
        self.assertEqual(rec["endReason"], "seat-stop")
        self.assertEqual(rec["outcome"], headless.OK)
        self.assertIsNone(rec["note"])
        self.assertTrue(rec["handoffWritten"])
        self.assertEqual(rec["rateLimit"]["resetsAt"], 1790403600)
        [u] = self.usage_lines()
        self.assertEqual(u["source"], "result")
        self.assertEqual((u["input_tokens"], u["output_tokens"]), (10, 20))
        self.assertEqual(u["total_cost_usd"], 0.0421)
        self.assertEqual(u["num_turns"], 3)
        self.assertEqual(u["rateLimit"]["unifiedWindows"]["five_hour"]["utilization"], 0.27)
        [rep] = self.hub_inbox()
        self.assertEqual(rep["from"], "yamato")
        self.assertIn(f"({item['id']}, 正常)", rep["text"])
        self.assertNotIn("SEAT-OUTPUT", rep["text"])        # never the seat's output (§7.2)
        kinds = [e["kind"] for e in events.read(self.shipdir, seat="researcher")]
        self.assertIn(events.SHIFT_START, kinds)
        self.assertIn(events.SHIFT_END, kinds)
        self.assertNotIn(events.SHIFT_FAILED, kinds)
        self.hub_wake.assert_called_once()
        self.assertTrue(list((self.shipdir / "seats/researcher/headless").glob("shift-1.jsonl")))

    def test_the_session_start_hook_really_ran_and_read_the_inbox(self):
        inbox.append(self.shipdir, "researcher", "pm", "T-001 を調べて")
        self.run_wrapper(p_seat_stop=True)
        self.assertEqual(inbox.unread(self.shipdir, "researcher"), [])

    def test_no_seat_stop_is_an_end_without_handoff_and_pastes_the_last_response(self):
        brd = board.Board(self.shipdir, self.team())
        item = brd.add("調べる", {"assignee": "researcher", "state": "active"})
        self.assertEqual(self.run_wrapper(), 0)
        rec = roster.seat(self.shipdir, "researcher")
        self.assertEqual(rec["endReason"], "exited")
        self.assertEqual(rec["outcome"], headless.NO_HANDOFF)
        self.assertEqual(rec["note"], roster.NO_HANDOFF_NOTE)
        _, body, _ = brd.read(item["id"])
        self.assertIn("引き継ぎなしで終了", body)
        self.assertIn(RESULT_TEXT, body)
        self.assertIn("引き継ぎなし)", self.hub_inbox()[0]["text"])
        end = events.read(self.shipdir, kinds=events.SHIFT_END)[-1]
        self.assertEqual(end["data"]["note"], roster.NO_HANDOFF_NOTE)

    # --- time limit ---

    def test_time_limit_sigterms_and_counts_usage_from_the_transcript(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text(encoding="utf-8").replace("    shift: headless\n", "    shift: headless\n    max_duration: 1s\n"), encoding="utf-8")
        seat.prepare(self.shipdir)
        load = seat.current_team

        def short_limit(shipdir):   # 1s is the shortest team.yaml can say
            team = load(shipdir)
            self.assertEqual(team["roles"]["researcher"]["max_duration"], 1)
            team["roles"]["researcher"]["max_duration"] = 0.2
            return team
        t0 = time.time()
        with mock.patch.object(seat, "current_team", short_limit):
            self.run_wrapper(p_sleep=30)
        self.assertLess(time.time() - t0, 15)
        rec = roster.seat(self.shipdir, "researcher")
        self.assertEqual(rec["endReason"], "max-duration")
        self.assertEqual(rec["outcome"], headless.TIMEOUT)
        # POSIX は SIGTERM の 143、Windows は CTRL_BREAK の STATUS_CONTROL_C_EXIT (0xC000013A)
        self.assertEqual(rec["exitCode"], 0xC000013A if os.name == "nt" else 143)
        [u] = self.usage_lines()
        self.assertEqual(u["source"], "transcript")
        self.assertIsNone(u["total_cost_usd"])
        self.assertEqual(u["messages"], 1)                   # same message.id twice -> once (V9)
        self.assertEqual(u["input_tokens"], 7)
        fs = events.read(self.shipdir, kinds=events.FORCE_STOP)
        self.assertEqual(fs[-1]["data"]["reason"], "max-duration")
        self.assertIn("時間切れ", self.hub_inbox()[0]["text"])

    def test_grace_end_moved_by_down_force_stops_the_shift(self):
        self.set_fake_mode(p_sleep=30)
        th = threading.Thread(target=headless.run, args=(self.shipdir, "researcher"))
        th.start()
        for _ in range(100):
            if roster.seat(self.shipdir, "researcher").get("pid"):
                break
            time.sleep(0.05)
        self.assertTrue(headless.running(self.shipdir, "researcher"))
        with mock.patch.object(seat.claude, "agents", return_value=[]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                seat.down(self.shipdir, force=True)
        th.join(15)
        self.assertFalse(th.is_alive())
        self.assertIn("researcher", buf.getvalue())
        rec = roster.seat(self.shipdir, "researcher")
        # the wrapper's own poll may see the moved grace end first
        self.assertIn(rec["endReason"], ("down-force", "grace-exceeded"))
        self.assertIn(rec["outcome"], (headless.FORCED, headless.TIMEOUT))
        self.assertEqual(len(events.read(self.shipdir, kinds=events.FORCE_STOP)), 1)

    # --- a wrapper that goes away must not leave claude -p without its time limit ---

    def _orphan(self):
        """A shift whose wrapper is gone but whose claude -p (a stand-in carrying its --session-id) runs."""
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "--session-id", sid],
                                 **procs.group_kwargs())
        self.addCleanup(_reap, child)
        roster.start_shift(self.shipdir, "researcher", session_id=sid, short_id=sid[:8],
                           session_name="t1.researcher", how="headless")
        roster.update(self.shipdir, "researcher", pid=child.pid, pidStart=procs.start_time(child.pid), group=True)
        return child

    def test_reconcile_stops_an_orphaned_p_and_closes_the_shift(self):
        child = self._orphan()
        seat.reconcile(self.shipdir, self.team(), [])
        self.assertIsNotNone(child.wait(10))
        rec = roster.seat(self.shipdir, "researcher")
        self.assertEqual((rec["state"], rec["endReason"]), (roster.OFF, "wrapper-lost"))
        [fs] = events.read(self.shipdir, kinds=events.FORCE_STOP)
        self.assertTrue(fs["data"]["orphan"])

    def test_force_stop_all_stops_an_orphaned_p(self):
        child = self._orphan()
        with mock.patch.object(seat.report, "safety_net", return_value=[]):
            stopped = seat.force_stop_all(self.shipdir, self.team(), "down-force")
        self.assertEqual(stopped, ["researcher"])
        self.assertIsNotNone(child.wait(10))
        self.assertEqual(roster.seat(self.shipdir, "researcher")["endReason"], "down-force")

    def test_windows_soft_stop_without_the_group_mark_is_a_hard_kill(self):
        # CTRL_BREAK は同じコンソール全体に届く。group の印が無い pid には送らず taskkill /T /F に回す (T-053)
        with mock.patch.object(procs, "is_windows", return_value=True), \
                mock.patch.object(procs, "hard_kill") as hk, mock.patch.object(procs.os, "kill") as kill:
            procs.soft_stop(123, group=False)
            hk.assert_called_once_with(123)
            kill.assert_not_called()

    def test_terminate_passes_the_group_mark_from_the_roster(self):
        rec = {"pid": 4242, "sessionId": "s", "pidStart": 1}
        for rec_group, expect in (({}, False), ({"group": True}, True)):
            with mock.patch.object(headless.procs, "terminate", return_value=True) as t, \
                    mock.patch.object(headless, "live_pid", return_value=4242), \
                    mock.patch.object(headless, "running", return_value=False), \
                    mock.patch.object(headless.roster, "seat", return_value={**rec, **rec_group, "state": roster.ON_SHIFT}), \
                    mock.patch.object(headless.roster, "update"), mock.patch.object(headless, "append_log"), \
                    mock.patch.object(headless.events, "emit"):
                headless.stop_orphan(self.shipdir, "researcher", "x")
            self.assertEqual(t.call_args.kwargs["group"], expect)

    def test_a_reused_pid_is_not_taken_for_the_p(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(_reap, other)
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        roster.start_shift(self.shipdir, "researcher", session_id=sid, short_id=sid[:8],
                           session_name="t1.researcher", how="headless")
        roster.update(self.shipdir, "researcher", pid=other.pid)
        self.assertFalse(headless.stop_orphan(self.shipdir, "researcher", "x"))
        self.assertIsNone(other.poll())

    @unittest.skipUnless(os.name == "posix", "SIGTERM to the wrapper is POSIX only (Windows: CTRL_BREAK, docs/verify/verify-win-w5.md)")
    def test_sigterm_to_the_wrapper_is_forwarded_and_the_shift_is_closed(self):
        self.set_fake_mode(p_sleep=60)
        yamato = Path(__file__).resolve().parents[1] / "yamato"
        wrapper = subprocess.Popen([sys.executable, str(yamato), "run-headless", str(self.shipdir), "researcher"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(_reap, wrapper)
        for _ in range(200):
            rec = roster.seat(self.shipdir, "researcher")
            if rec.get("pid") and headless.live_pid(rec):
                break
            time.sleep(0.05)
        pid = rec["pid"]
        wrapper.send_signal(signal.SIGTERM)
        wrapper.wait(20)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        rec = roster.seat(self.shipdir, "researcher")
        self.assertEqual((rec["state"], rec["endReason"]), (roster.OFF, "wrapper-signal"))

    # --- failures (V1, V5) ---

    def test_no_session_start_hook_is_a_failure(self):
        self.assertEqual(self.run_wrapper(p_no_hook=True, p_seat_stop=True), 1)
        rec = roster.seat(self.shipdir, "researcher")
        self.assertEqual(rec["outcome"], headless.FAILED)
        self.assertEqual(rec["endReason"], "failed")
        [f] = events.read(self.shipdir, kinds=events.SHIFT_FAILED)
        self.assertIn("SessionStart", f["summary"])
        self.assertIn("異常: SessionStart hook なし", self.hub_inbox()[0]["text"])

    def test_the_hook_mark_is_judged_only_once_the_run_got_going(self):
        st = headless._Stream([], self.tmp / "x.jsonl")
        self.assertEqual(headless._failures(st, 143, "max-duration", None), [])   # killed before the hook
        st.init = True
        self.assertEqual([w for w, _ in headless._failures(st, 143, "max-duration", None)], ["SessionStart hook なし"])
        st.hook_ok = True
        self.assertEqual(headless._failures(st, 143, "max-duration", None), [])
        self.assertEqual([w for w, _ in headless._failures(st, 1, None, None)], ["結果なしで終了 exit 1"])

    def test_api_error_is_judged_by_is_error_not_subtype(self):
        self.assertEqual(self.run_wrapper(p_api_error=429), 1)
        rec = roster.seat(self.shipdir, "researcher")
        self.assertEqual(rec["outcome"], headless.FAILED)
        [f] = events.read(self.shipdir, kinds=events.SHIFT_FAILED)
        self.assertEqual(f["data"]["api_error_status"], 429)
        text = self.hub_inbox()[0]["text"]
        self.assertIn("API エラー 429 (枠切れの可能性)", text)
        self.assertNotIn("Rate limit reached", text)

    def test_no_result_line_is_a_failure_and_is_not_retried(self):
        inbox.append(self.shipdir, "researcher", "pm", "x")
        self.set_fake_mode(p_no_result=True, p_inbox_once="during")
        self.assertEqual(headless.run(self.shipdir, "researcher"), 1)
        self.assertEqual(len(self.p_calls()), 1)
        self.assertEqual(roster.seat(self.shipdir, "researcher")["outcome"], headless.FAILED)

    # --- one shift at a time, and the next one (§4.3) ---

    def test_mail_that_came_in_during_the_shift_starts_the_next_shift(self):
        self.run_wrapper(p_seat_stop=True, p_inbox_once="追加の依頼")
        self.assertEqual(len(self.p_calls()), 2)
        self.assertEqual(roster.seat(self.shipdir, "researcher")["shiftNo"], 2)
        self.assertEqual(inbox.unread(self.shipdir, "researcher"), [])

    def test_a_second_wrapper_waits_and_runs_only_for_unread_mail(self):
        lock = headless._try_lock(headless._lock_path(self.shipdir, "researcher", "lock"))
        th = threading.Thread(target=headless.run, args=(self.shipdir, "researcher"))
        th.start()
        time.sleep(0.15)
        # a third one finds the waiter and leaves at once
        self.assertEqual(headless.run(self.shipdir, "researcher"), 0)
        self.assertTrue(th.is_alive())
        headless._release(lock)
        th.join(10)
        self.assertEqual(self.p_calls(), [])                 # nothing unread: no shift

    def test_not_running_ship_starts_no_shift(self):
        deadline.end_now(self.shipdir)
        inbox.append(self.shipdir, "researcher", "pm", "x")
        self.assertEqual(self.run_wrapper(), 0)
        self.assertEqual(self.p_calls(), [])

    # --- send / seat-stop / settings ---

    def test_send_to_a_headless_seat_spawns_the_wrapper_and_returns(self):
        spawned = []
        with mock.patch.object(headless, "spawn", side_effect=lambda s, n: spawned.append(n)):
            with mock.patch.object(seat, "wake", side_effect=lambda sd, t, s, reason="send": headless.wake(sd, t, s)):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    seat.send(self.shipdir, "researcher", "調べて", "pm")
                self.assertIn("シフトを起動した", buf.getvalue())
                lock = headless._try_lock(headless._lock_path(self.shipdir, "researcher", "lock"))
                buf = io.StringIO()
                with redirect_stdout(buf):
                    seat.send(self.shipdir, "researcher", "もう 1 件", "pm")
                headless._release(lock)
                self.assertIn("シフト中。inbox に積んだ", buf.getvalue())
        self.assertEqual(spawned, ["researcher", "researcher"])
        self.assertEqual(len(inbox.unread(self.shipdir, "researcher")), 2)
        self.assertEqual(self.p_calls(), [])

    def test_up_with_a_headless_hub_spawns_the_wrapper(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text(encoding="utf-8").replace("hub: pm", "hub: researcher"), encoding="utf-8")
        with mock.patch.object(seat, "spawn_watchdog"), \
                mock.patch.object(seat, "wake", side_effect=lambda sd, t, s, reason="send": headless.wake(sd, t, s)), \
                mock.patch.object(headless, "spawn") as spawn:
            buf = io.StringIO()
            with redirect_stdout(buf):
                seat.up(self.shipdir, "10m")
        spawn.assert_called_once_with(self.shipdir, "researcher")
        self.assertIn("headless のシフトを起動した", buf.getvalue())

    def test_seat_stop_on_a_headless_seat_does_not_schedule_a_stop(self):
        sid = "11111111-2222-3333-4444-555555555555"
        roster.start_shift(self.shipdir, "researcher", session_id=sid, short_id=sid[:8],
                           session_name="t1.researcher", how="headless")
        (self.shipdir / "seats/researcher/handoff.md").write_text("x\n", encoding="utf-8")
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_SESSION_ID", None)
        with mock.patch.object(seat, "_spawn_detached") as spawn:
            buf = io.StringIO()
            with redirect_stdout(buf):
                seat.seat_stop(self.shipdir, "researcher", 10)
        spawn.assert_not_called()
        self.assertEqual(roster.seat(self.shipdir, "researcher")["state"], roster.STOPPING)
        self.assertIn("headless", buf.getvalue())


class HeadlessOptionsTest(_Base):
    role_extra = """  checker:
    model: sonnet
    shift: headless
    max_budget_usd: 0.25
    report_to: owner
"""
    extra_roles = ("checker",)

    def test_budget_flag_and_report_to_owner(self):
        headless.run(self.shipdir, "checker")
        [call] = self.p_calls()
        a = call["argv"]
        self.assertEqual(a[a.index("--max-budget-usd") + 1], "0.25")
        [rep] = inbox.entries(self.shipdir, "owner")
        self.assertIn("checker の headless シフト #1 が終了", rep["text"])
        self.assertEqual(self.hub_inbox(), [])


if __name__ == "__main__":
    unittest.main()
