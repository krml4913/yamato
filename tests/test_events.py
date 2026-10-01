"""events.jsonl (design-p1 §0.1) and roster lastActive (§5.1)."""
import io
import json
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import board, deadline, events, hooks, roster, runtime, seat


class EventsTest(ShipTestCase):
    def test_emit_writes_one_line_in_the_documented_shape(self):
        line = events.emit(self.shipdir, "custom_kind", seat="impl", item="T-001", by="pm",
                           summary="a\nb  " + "x" * 300, data={"k": 1})
        [raw] = (self.shipdir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        e = json.loads(raw)
        self.assertEqual(e, line)
        self.assertEqual({k: e[k] for k in ("kind", "seat", "item", "by", "data")},
                         {"kind": "custom_kind", "seat": "impl", "item": "T-001", "by": "pm", "data": {"k": 1}})
        self.assertTrue(e["summary"].startswith("a b x"))  # one line
        self.assertEqual(len(e["summary"]), events.SUMMARY_CHARS + 1)
        self.assertAlmostEqual(e["ts"], time.time(), delta=5)

    def test_read_filters_by_period_kind_seat_and_item(self):
        t = 1_000_000.0
        events.emit(self.shipdir, events.SEND, seat="impl", now=t)
        events.emit(self.shipdir, events.BOARD_SET, seat="impl", item="T-001", now=t + 10)
        events.emit(self.shipdir, events.BOARD_ADD, seat="pm", item="T-002", now=t + 20)
        with open(self.shipdir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write('{"torn\n[1]\n\n')
        events.emit(self.shipdir, events.FORCE_STOP, seat="pm", now=t + 30)

        kinds = lambda es: [e["kind"] for e in es]
        self.assertEqual(len(events.read(self.shipdir)), 4)
        self.assertEqual(kinds(events.read(self.shipdir, since=t + 10, until=t + 30)),
                         [events.BOARD_SET, events.BOARD_ADD])
        self.assertEqual(kinds(events.read(self.shipdir, kinds=events.SEND)), [events.SEND])
        self.assertEqual(kinds(events.read(self.shipdir, kinds=[events.BOARD_ADD, events.BOARD_SET])),
                         [events.BOARD_SET, events.BOARD_ADD])
        self.assertEqual(kinds(events.read(self.shipdir, seat="pm")), [events.BOARD_ADD, events.FORCE_STOP])
        self.assertEqual(kinds(events.read(self.shipdir, item="T-001")), [events.BOARD_SET])

    def test_read_without_a_file(self):
        self.assertEqual(events.read(self.shipdir), [])

    def test_a_failed_append_does_not_raise(self):
        with mock.patch("builtins.open", side_effect=OSError("disk full")), redirect_stderr(io.StringIO()) as err:
            self.assertIsNone(events.emit(self.shipdir, events.SEND))
        self.assertIn("disk full", err.getvalue())

    def test_emit_takes_the_ship_lock(self):
        from yamato import util

        seen = []
        real = util.ship_lock

        def spy(shipdir):
            seen.append(shipdir)
            return real(shipdir)

        with mock.patch.object(events, "ship_lock", spy):
            events.emit(self.shipdir, events.SEND)
        self.assertEqual(seen, [self.shipdir])

    def test_board_changes(self):
        brd = board.Board(self.shipdir, self.team())
        brd.add("関数を足す", {"assignee": "impl"}, by="pm")
        brd.set("T-001", {"state": "active"}, by="impl")
        brd.set("T-001", {}, note="半分できた", by="impl")
        brd.set("T-001", {"state": "done"}, by="pm")
        add, active, note, done = events.read(self.shipdir, item="T-001")
        self.assertEqual((add["kind"], add["seat"], add["by"]), (events.BOARD_ADD, "impl", "pm"))
        self.assertIn("関数を足す", add["summary"])
        self.assertEqual(active["kind"], events.BOARD_SET)
        self.assertEqual(active["data"]["changes"]["state"], ["open", "active"])
        self.assertIn("state: open→active", active["summary"])
        self.assertEqual(note["data"]["note"], "半分できた")
        self.assertEqual(done["data"]["changes"]["state"], ["active", "done"])

    def test_board_archive(self):
        t = self.team()
        t["board"]["archive_on_done"] = False
        brd = board.Board(self.shipdir, t)
        brd.add("x", {"state": "done"})
        brd.archive()
        self.assertEqual([e["kind"] for e in events.read(self.shipdir)], [events.BOARD_ADD, events.BOARD_ARCHIVE])

    def test_shift_start_and_end(self):
        roster.start_shift(self.shipdir, "impl", session_id="s" * 36, short_id="ssssssss",
                           session_name="t1.impl", how="new", now=100.0)
        roster.end_shift(self.shipdir, "impl", reason="exited", handoff_written=False,
                         note=roster.NO_HANDOFF_NOTE, now=200.0)
        start, end = events.read(self.shipdir, seat="impl")
        self.assertEqual((start["kind"], start["ts"], start["data"]["how"], start["data"]["shiftNo"]),
                         (events.SHIFT_START, 100.0, "new", 1))
        self.assertEqual((end["kind"], end["ts"], end["data"]["reason"], end["data"]["note"]),
                         (events.SHIFT_END, 200.0, "exited", roster.NO_HANDOFF_NOTE))
        self.assertIn(roster.NO_HANDOFF_NOTE, end["summary"])

    def test_decision_kinds_are_ready_for_p1_2(self):
        events.emit(self.shipdir, events.DECISION_OPEN, seat="pm", item="D-001", by="impl", summary="認証方式")
        events.emit(self.shipdir, events.DECISION_CLOSE, seat="pm", item="D-001", by="pm", summary="B")
        self.assertEqual(len(events.read(self.shipdir, kinds=[events.DECISION_OPEN, events.DECISION_CLOSE],
                                         item="D-001")), 2)


class SeatEventsTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        for name in ("spawn_watchdog", "_spawn_detached"):
            p = mock.patch.object(seat, name)
            p.start()
            self.addCleanup(p.stop)

    def run_cmd(self, fn, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(*args)
        return buf.getvalue()

    def test_send_is_recorded_even_before_up(self):
        self.run_cmd(seat.send, self.shipdir, "impl", "T-001 を頼む", "pm")
        self.run_cmd(seat.send, self.shipdir, "owner", "判断ください", "pm")
        a, b = events.read(self.shipdir, kinds=events.SEND)
        self.assertEqual((a["seat"], a["by"], a["summary"], a["data"]["n"]), ("impl", "pm", "T-001 を頼む", 1))
        self.assertEqual((b["seat"], b["by"]), ("owner", "pm"))

    def test_up_send_and_force_stop(self):
        self.run_cmd(seat.up, self.shipdir, "20m")
        self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm")
        self.run_cmd(seat.down, self.shipdir, True)
        kinds = [(e["kind"], e["seat"]) for e in events.read(self.shipdir)]
        self.assertEqual(kinds[:3], [(events.SHIFT_START, "pm"), (events.SEND, "impl"), (events.SHIFT_START, "impl")])
        stops = events.read(self.shipdir, kinds=events.FORCE_STOP)
        self.assertEqual(sorted(e["seat"] for e in stops), ["impl", "pm"])
        self.assertEqual({e["data"]["reason"] for e in stops}, {"down-force"})
        ends = events.read(self.shipdir, kinds=events.SHIFT_END)
        self.assertEqual({e["data"]["reason"] for e in ends}, {"down-force"})

    def test_status_shows_last_active_from_the_hooks(self):
        self.run_cmd(seat.up, self.shipdir, "20m")
        t = time.time() + 3600  # later than anything else the seat has
        roster.touch(self.shipdir, "pm", now=t)
        out = self.run_cmd(seat.status, self.shipdir)
        pm_line = next(line for line in out.splitlines() if line.strip().startswith("pm "))
        self.assertIn(time.strftime("%m-%d %H:%M:%S", time.localtime(t)), pm_line)


class LastActiveHookTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        runtime.generate(self.shipdir, self.team())
        roster.start_shift(self.shipdir, "impl", session_id="s" * 36, short_id="ssssssss",
                           session_name="t1.impl", how="new", now=100.0)

    def run_hook(self, fn, stdin: dict):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(stdin))), redirect_stdout(out), \
                redirect_stderr(io.StringIO()):
            code = fn(self.shipdir, "impl")
        return code, out.getvalue()

    def last_active(self):
        return roster.seat(self.shipdir, "impl")["lastActive"]

    def test_shift_start_sets_it(self):
        self.assertEqual(self.last_active(), 100.0)

    def test_session_start_user_prompt_and_stop_update_it(self):
        for fn in (hooks.session_start, hooks.user_prompt_submit, hooks.stop):
            with self.subTest(fn=fn.__name__):
                roster.touch(self.shipdir, "impl", now=1.0)
                self.assertEqual(self.run_hook(fn, {})[0], 0)
                self.assertAlmostEqual(self.last_active(), time.time(), delta=5)

    def test_user_prompt_submit_is_wired_and_silent(self):
        s = json.loads(runtime.settings_path(self.shipdir, "impl").read_text(encoding="utf-8"))
        [h] = [h for grp in s["hooks"]["UserPromptSubmit"] for h in grp["hooks"]]
        self.assertEqual(h["args"][1:3], ["hook", "user-prompt-submit"])
        self.assertIn("user-prompt-submit", hooks.HOOKS)
        self.assertEqual(self.run_hook(hooks.user_prompt_submit, {"prompt": "hi"})[1], "")

    def test_stop_hook_still_blocks_when_touch_fails(self):
        deadline.write(self.shipdir, limit=0, grace=600, token="t", now=time.time() - 1)
        with mock.patch.object(roster, "touch", side_effect=OSError("ro")):
            code, out = self.run_hook(hooks.stop, {})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_permission_denials_are_recorded(self):
        _, out = self.run_hook(hooks.deny_dialog, {"tool_name": "Write", "tool_input": {"file_path": "x"}})
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["decision"]["behavior"], "deny")
        self.run_hook(hooks.log_denied, {"tool_name": "Bash", "tool_input": {"command": "rm"}, "reason": "risky"})
        dialog, auto = events.read(self.shipdir, kinds=events.PERMISSION_DENIED)
        self.assertEqual((dialog["seat"], dialog["data"]["source"], dialog["data"]["tool"]), ("impl", "dialog", "Write"))
        self.assertIn("Write", dialog["summary"])
        self.assertEqual((auto["data"]["source"], auto["data"]["reason"]), ("auto", "risky"))


if __name__ == "__main__":
    unittest.main()
