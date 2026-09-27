"""P1-5/7: rotation, spin detection, orphans, the captain gap, the last call and
``send --cwd`` (design-p1 §5.3-5.6, §9, §8.2 の 2), against tests/fake_claude.py."""
import io
import json
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import board, cli, deadline, events, headless, hooks, inbox, inject, report, roster, rotate, runtime, seat
from yamato.team import context_window, rotate_conf, validate
from yamato.util import YamatoError


def _assistant(tokens: int, model: str = "claude-sonnet-5", mid: str = "m") -> str:
    return json.dumps({"type": "assistant", "message": {
        "id": mid, "model": model, "role": "assistant",
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": tokens - 10 - 5,
                  "cache_read_input_tokens": 5, "output_tokens": 99}}})


class _Base(ShipTestCase):
    def setUp(self):
        super().setUp()
        for target, attr in ((seat, "spawn_watchdog"), (seat, "_spawn_detached"), (headless, "spawn")):
            p = mock.patch.object(target, attr)
            p.start()
            self.addCleanup(p.stop)

    def run_cmd(self, fn, *args, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(*args, **kw)
        return buf.getvalue()

    def up(self, for_="3h"):
        return self.run_cmd(seat.up, self.shipdir, for_)

    def bg_calls(self):
        return [c for c in self.fake()["calls"] if "--bg" in c["argv"] and "--resume" not in c["argv"]]

    def resume_calls(self):
        return [c for c in self.fake()["calls"] if "--resume" in c["argv"]]

    def stop_session(self, name):
        st = self.fake()
        for s in st["sessions"]:
            if s["sessionId"] == roster.seat(self.shipdir, name)["sessionId"]:
                s["pid"] = None
        self.fake_state.write_text(json.dumps(st))

    def end_shift(self, name, ago=0.0, handoff=True):
        self.stop_session(name)
        roster.end_shift(self.shipdir, name, reason="seat-stop", handoff_written=handoff,
                         now=time.time() - ago)

    def transcript(self, sid: str, *lines: str) -> Path:
        path = self.config / "projects" / "-ws" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(x + "\n" for x in lines))
        return path

    def kinds(self, kind):
        return events.read(self.shipdir, kinds=kind)


# --- team.yaml ---------------------------------------------------------------------

class SettingsTest(_Base):
    def conf(self, extra: str) -> dict:
        import yaml

        data = yaml.safe_load((self.shipdir / "team.yaml").read_text())
        data.update(yaml.safe_load(extra) or {})
        return validate(data, self.shipdir)

    def test_template_defaults(self):
        t = self.team()
        self.assertEqual(rotate_conf(t, "pm"), {"context": {"ratio": 0.3}, "compaction": True, "hours": 8 * 3600,
                                                "idle": 3600, "new_day": True})
        self.assertEqual(t["watch"]["spin"], {"window": 600, "max_sends": 6, "same_text": True})
        self.assertEqual((t["watch"]["captain_gap"], t["watch"]["orphan_after"]), (1800, 1800))
        self.assertEqual(t["last_call"], {"at_most": 1800, "ratio": 0.2})
        self.assertIn("orphans", t["roles"]["pm"]["inject"])

    def test_rotate_values_and_off(self):
        import yaml

        data = yaml.safe_load((self.shipdir / "team.yaml").read_text())
        data["roles"]["pm"]["rotate"] = {"context": "300k", "compaction": False, "hours": "90m", "idle": "off",
                                         "new_day": False}
        t = validate(data, self.shipdir)
        self.assertEqual(rotate_conf(t, "pm"), {"context": {"tokens": 300000}, "compaction": False, "hours": 5400,
                                                "idle": None, "new_day": False})
        data["roles"]["pm"]["rotate"] = {"context": False, "hours": 2}
        self.assertEqual(rotate_conf(validate(data, self.shipdir), "pm")["hours"], 7200)   # bare number = hours
        data["roles"]["pm"]["rotate"] = {"context": 0.25}
        self.assertEqual(rotate_conf(validate(data, self.shipdir), "pm")["context"], {"ratio": 0.25})
        for bad in ({"context": "150%"}, {"nope": 1}, {"compaction": "yes"}, {"hours": 0}):
            data["roles"]["pm"]["rotate"] = bad
            with self.assertRaises(YamatoError):
                validate(data, self.shipdir)

    def test_a_role_without_rotate_gets_the_fallback(self):
        t = self.team()
        self.assertEqual(rotate_conf(t, "impl")["context"], {"ratio": 0.3})
        old = json.loads(json.dumps(t))
        del old["roles"]["pm"]["rotate"]
        self.assertEqual(rotate_conf(old, "pm")["idle"], 3600)   # a team.json from before P1-5

    def test_watch_last_call_and_windows_can_be_turned_off_or_changed(self):
        t = self.conf("watch: {spin: off, captain_gap: off}\nlast_call: off\ncontext_windows: {fable: 500k}")
        self.assertIsNone(t["watch"]["spin"])
        self.assertIsNone(t["watch"]["captain_gap"])
        self.assertEqual(t["watch"]["stale_after"], 1200)
        self.assertIsNone(t["last_call"])
        self.assertEqual(context_window(t, "claude-fable-5-1"), 500000)
        self.assertEqual(context_window(t, "claude-haiku-4-5"), 200000)
        self.assertIsNone(context_window(t, "gpt"))
        with self.assertRaises(YamatoError):
            self.conf("watch: {spin: {max_sends: 0}}")


# --- rotation -------------------------------------------------------------------------

class TranscriptTest(_Base):
    def test_last_usage_reads_the_tail_and_skips_other_lines(self):
        path = self.transcript("x", _assistant(1000, mid="a"), _assistant(250_000, mid="b"),
                               json.dumps({"type": "user", "message": {"content": "hi"}}), "{torn")
        self.assertEqual(rotate.last_usage(path), {"tokens": 250_000, "model": "claude-sonnet-5"})
        self.assertIsNone(rotate.last_usage(self.tmp / "missing.jsonl"))

    def test_last_usage_across_chunks(self):
        filler = json.dumps({"type": "user", "message": {"content": "x" * 1000}})
        path = self.transcript("y", _assistant(42_000), *([filler] * 600))   # ~600 KB after the usage
        self.assertEqual(rotate.last_usage(path)["tokens"], 42_000)


class ResumeOrNewShiftTest(_Base):
    """send to a stopped persistent seat (design-p1 §5.3)."""

    def setUp(self):
        super().setUp()
        self.up()
        self.sid = roster.seat(self.shipdir, "pm")["sessionId"]

    def send(self, text="x", sender="impl"):
        return self.run_cmd(seat.send, self.shipdir, "pm", text, sender)

    def test_resume_when_no_condition_holds(self):
        self.end_shift("pm")
        out = self.send()
        self.assertEqual(len(self.resume_calls()), 1)
        self.assertIn("resume した", out)

    def test_rotate_mark_starts_a_new_shift_and_is_used_up(self):
        self.end_shift("pm")
        roster.update(self.shipdir, "pm", rotateRequested=True)
        out = self.send()
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual(len(self.bg_calls()), 2)
        rec = roster.seat(self.shipdir, "pm")
        self.assertNotEqual(rec["sessionId"], self.sid)
        self.assertEqual(rec["how"], "new")
        self.assertIsNone(rec["rotateRequested"])
        self.assertIn("入れ替え: 入れ替えの印", out)
        [start] = [e for e in self.kinds(events.SHIFT_START) if e["data"]["shiftNo"] == 2]
        self.assertEqual(start["data"]["rotated"], ["入れ替えの印 (seat-stop --rotate)"])

    def test_idle_for_an_hour_starts_a_new_shift(self):
        self.end_shift("pm", ago=3700)
        self.assertIn("止まってから 1 時間", self.send())
        self.assertEqual(self.resume_calls(), [])

    def test_a_new_day_starts_a_new_shift(self):
        self.end_shift("pm")
        roster.update(self.shipdir, "pm", shiftStartedAt=time.time() - 86400 * 2)
        self.assertIn("日付が変わった", self.send())

    def test_large_context_starts_a_new_shift(self):
        self.transcript(self.sid, _assistant(310_000, model="claude-opus-5-5"))
        self.end_shift("pm")
        self.assertIn("コンテキスト 310k ≥ 300k", self.send())
        self.assertEqual(self.resume_calls(), [])

    def test_small_context_and_conditions_off_resume(self):
        self.transcript(self.sid, _assistant(290_000, model="claude-opus-5-5"))
        self.end_shift("pm", ago=7200)
        team = json.loads((self.shipdir / ".runtime" / "team.json").read_text())
        team["roles"]["pm"]["rotate"].update(idle=None)
        (self.shipdir / ".runtime" / "team.json").write_text(json.dumps(team))
        self.send()
        self.assertEqual(len(self.resume_calls()), 1)

    def test_up_uses_the_same_rule(self):
        self.end_shift("pm", ago=3700)
        out = self.up()
        self.assertEqual(self.resume_calls(), [])
        self.assertIn("新しいシフトを起動した", out)


class SeatStopRotateTest(_Base):
    def test_seat_stop_rotate_sets_the_mark_and_wakes_nothing(self):
        self.up()
        rec = roster.seat(self.shipdir, "pm")
        (self.shipdir / "seats" / "pm" / "handoff.md").write_text("次: T-001")
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": rec["sessionId"]}):
            out = self.run_cmd(seat.seat_stop, self.shipdir, "pm", 10, False, True)
        self.assertIn("入れ替えの印を立てた", out)
        self.assertTrue(roster.seat(self.shipdir, "pm")["rotateRequested"])
        self.assertEqual(len(self.bg_calls()), 1)
        self.assertEqual(len(self.kinds(events.ROTATE_REQUESTED)), 1)
        self.stop_session("pm")
        seat.shift_ended(self.shipdir, "pm", rec["sessionId"])
        self.assertTrue(roster.seat(self.shipdir, "pm")["rotateRequested"])   # survives the end of the shift
        self.run_cmd(seat.send, self.shipdir, "pm", "x", "impl")
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual(len(self.bg_calls()), 2)


class RotateCommandTest(_Base):
    """``yamato rotate <ship> <seat>...`` (T-024): the same mark as ``seat-stop --rotate``,
    but set from outside for a *stopped* persistent seat (no session to run it from)."""

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_marks_a_stopped_persistent_seat(self):
        self.up()
        self.end_shift("pm")
        rc, out, _ = self.cli("rotate", str(self.shipdir), "pm")
        self.assertEqual(rc, 0)
        self.assertIn("pm: 入れ替えの印を立てた", out)
        self.assertTrue(roster.seat(self.shipdir, "pm")["rotateRequested"])
        [ev] = self.kinds(events.ROTATE_REQUESTED)
        self.assertEqual(ev["seat"], "pm")
        self.assertEqual(ev["by"], "owner")   # no CLAUDE_CODE_SESSION_ID in the test process
        # the mark is then read exactly like seat-stop --rotate (§5.3 の 1)
        out2 = self.send()
        self.assertEqual(self.resume_calls(), [])
        self.assertIn("入れ替え: 入れ替えの印", out2)

    def send(self, text="x", sender="impl"):
        return self.run_cmd(seat.send, self.shipdir, "pm", text, sender)

    def test_all_marks_every_stopped_persistent_seat_only(self):
        self.up()
        self.end_shift("pm")
        rc, out, _ = self.cli("rotate", str(self.shipdir), "--all")
        self.assertEqual(rc, 0)
        self.assertTrue(roster.seat(self.shipdir, "pm")["rotateRequested"])
        # impl is per_task, not persistent: --all never touches it
        self.assertIsNone(roster.seat(self.shipdir, "impl").get("rotateRequested"))

    def test_refuses_a_live_seat_with_a_reason(self):
        self.up()
        rc, out, _ = self.cli("rotate", str(self.shipdir), "pm")
        self.assertEqual(rc, 0)
        self.assertIn("pm: 立てなかった (生きている", out)
        self.assertFalse(roster.seat(self.shipdir, "pm").get("rotateRequested"))
        self.assertEqual(self.kinds(events.ROTATE_REQUESTED), [])

    def test_refuses_a_per_task_seat_with_a_reason(self):
        rc, out, _ = self.cli("rotate", str(self.shipdir), "impl")
        self.assertEqual(rc, 0)
        self.assertIn("impl: 立てなかった (shift: per_task", out)
        self.assertIsNone(roster.seat(self.shipdir, "impl").get("rotateRequested"))

    def test_unknown_seat_is_a_clean_error(self):
        rc, _, err = self.cli("rotate", str(self.shipdir), "nope")
        self.assertEqual(rc, 1)
        self.assertIn("席がありません", err)

    def test_all_and_a_seat_name_together_is_an_error(self):
        rc, _, err = self.cli("rotate", str(self.shipdir), "pm", "--all")
        self.assertEqual(rc, 1)
        self.assertIn("両方はできません", err)

    def test_neither_a_seat_nor_all_is_an_error(self):
        rc, _, err = self.cli("rotate", str(self.shipdir))
        self.assertEqual(rc, 1)
        self.assertIn("--all", err)

    def test_status_flags_the_mark(self):
        self.up()
        self.end_shift("pm")
        roster.update(self.shipdir, "pm", rotateRequested=True)
        out = self.run_cmd(seat.status, self.shipdir)
        self.assertIn("次は新しいシフト", out)


class HookTest(_Base):
    def setUp(self):
        super().setUp()
        runtime.generate(self.shipdir, self.team())
        deadline.write(self.shipdir, limit=3 * 3600, grace=60, token="t", last_call=self.team()["last_call"])
        roster.start_shift(self.shipdir, "pm", session_id="p" * 36, short_id="pppppppp",
                           session_name="t1.pm", how="new")
        roster.start_shift(self.shipdir, "impl", session_id="s" * 36, short_id="ssssssss",
                           session_name="t1.impl", how="new")

    def run_hook(self, fn, stdin: dict, seat_name="pm"):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(stdin))), redirect_stdout(out), redirect_stderr(err):
            code = fn(self.shipdir, seat_name)
        return code, out.getvalue(), err.getvalue()

    def test_settings_have_the_pre_compact_hook(self):
        s = runtime.build_settings(self.shipdir, self.team(), "pm")
        self.assertIn("pre-compact", s["hooks"]["PreCompact"][0]["hooks"][0]["command"])

    def test_stop_hook_nudges_once_per_shift_on_context(self):
        path = self.transcript("p" * 36, _assistant(400_000))
        outs = [self.run_hook(hooks.stop, {"transcript_path": str(path)})[1] for _ in range(3)]
        self.assertEqual(sum(1 for o in outs if o), 1)
        msg = json.loads(outs[0])
        self.assertEqual(msg["decision"], "block")
        self.assertIn("seat-stop", msg["reason"])
        self.assertIn("--rotate", msg["reason"])
        self.assertIn("コンテキスト 400k", msg["reason"])
        self.assertEqual(len(self.kinds(events.ROTATE_SUGGESTED)), 1)
        roster.start_shift(self.shipdir, "pm", session_id="q" * 36, short_id="qqqqqqqq",
                           session_name="t1.pm", how="new")
        self.assertIn("--rotate", self.run_hook(hooks.stop, {"transcript_path": str(path)})[1])   # a new shift

    def test_stop_hook_quiet_under_threshold_and_for_per_task(self):
        path = self.transcript("p" * 36, _assistant(100_000))
        self.assertEqual(self.run_hook(hooks.stop, {"transcript_path": str(path)})[1], "")
        big = self.transcript("s" * 36, _assistant(900_000))
        self.assertEqual(self.run_hook(hooks.stop, {"transcript_path": str(big)}, "impl")[1], "")

    def test_pre_compact_marks_the_shift_and_the_stop_hook_nudges(self):
        self.run_hook(hooks.pre_compact, {"trigger": "auto"})
        self.assertEqual(roster.seat(self.shipdir, "pm")["compactedShift"], 1)
        self.assertIn("compaction が起きた", self.run_hook(hooks.stop, {})[1])

    def test_long_shift_nudges(self):
        roster.update(self.shipdir, "pm", shiftStartedAt=time.time() - 9 * 3600)
        self.assertIn("シフトが 8 時間を超えた", self.run_hook(hooks.stop, {})[1])

    def test_wrap_up_wins_over_rotation(self):
        deadline.write(self.shipdir, limit=0, grace=600, token="t", now=time.time() - 1)
        roster.update(self.shipdir, "pm", shiftStartedAt=time.time() - 9 * 3600)
        reason = json.loads(self.run_hook(hooks.stop, {})[1])["reason"]
        self.assertIn("上限を過ぎました", reason)

    # --- last call (design-p1 §9) ---

    def past_last_call(self):
        # 3h: the last call is min(30m, 36m) = 30m before; 20 minutes are left
        deadline.write(self.shipdir, limit=3 * 3600, grace=60, token="t", now=time.time() - 3 * 3600 + 1200,
                       last_call=self.team()["last_call"])

    def test_last_call_at_is_min_of_at_most_and_ratio(self):
        dl = deadline.write(self.shipdir, limit=3600, grace=60, token="t", now=1000.0,
                            last_call={"at_most": 1800, "ratio": 0.2})
        self.assertEqual(dl["deadline"] - dl["lastCallAt"], 720)   # 20% of 1h < 30m
        dl = deadline.write(self.shipdir, limit=3 * 3600, grace=60, token="t", now=1000.0,
                            last_call={"at_most": 1800, "ratio": 0.2})
        self.assertEqual(dl["deadline"] - dl["lastCallAt"], 1800)
        self.assertNotIn("lastCallAt", deadline.write(self.shipdir, limit=60, grace=0, token="t", last_call=None))

    def test_extend_moves_the_last_call_and_it_is_given_again(self):
        self.past_last_call()
        self.assertIsNotNone(deadline.take_last_call_notice(self.shipdir))
        self.assertIsNone(deadline.take_last_call_notice(self.shipdir))
        dl, _ = deadline.extend(self.shipdir, 3600)
        self.assertEqual(dl["deadline"] - dl["lastCallAt"], 1800)
        self.assertFalse(deadline.in_last_call(dl))

    def test_captain_first_turn_gets_the_notice_once(self):
        self.past_last_call()
        _, out, _ = self.run_hook(hooks.user_prompt_submit, {})
        ctx = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "UserPromptSubmit")
        self.assertIn("最終受付を過ぎました (艦の終了まで 20 分)", ctx["additionalContext"])
        self.assertEqual(self.run_hook(hooks.user_prompt_submit, {})[1], "")
        self.assertEqual(self.run_hook(hooks.stop, {})[1], "")
        self.assertEqual(len(self.kinds(events.LAST_CALL)), 1)

    def test_members_get_no_last_call_notice(self):
        self.past_last_call()
        self.assertEqual(self.run_hook(hooks.user_prompt_submit, {}, "impl")[1], "")
        self.assertEqual(self.run_hook(hooks.stop, {}, "impl")[1], "")

    def test_stop_hook_gives_the_notice_when_no_prompt_hook_did(self):
        self.past_last_call()
        reason = json.loads(self.run_hook(hooks.stop, {})[1])["reason"]
        self.assertIn("最終受付", reason)
        self.assertEqual(self.run_hook(hooks.user_prompt_submit, {})[1], "")

    def test_session_start_gives_the_notice(self):
        self.past_last_call()
        _, out, _ = self.run_hook(hooks.session_start, {"source": "startup"})
        self.assertTrue(json.loads(out)["hookSpecificOutput"]["additionalContext"].startswith("[yamato] 最終受付"))

    def test_before_the_last_call_nothing(self):
        self.assertEqual(self.run_hook(hooks.user_prompt_submit, {})[1], "")


class LastCallSendTest(_Base):
    def test_captain_send_gets_the_prefix_and_members_do_not(self):
        self.up("3h")
        dl = deadline.read(self.shipdir)
        dl["lastCallAt"] = time.time() - 1
        deadline.write_raw(self.shipdir, dl)
        out = self.run_cmd(seat.send, self.shipdir, "impl", "T-001 を片付けて", "pm")
        self.assertIn("本文の先頭に", out)
        [e] = inbox.entries(self.shipdir, "impl")
        self.assertRegex(e["text"], r"^\(終了まで \d+ 分。片付く範囲で\) T-001 を片付けて$")
        self.run_cmd(seat.send, self.shipdir, "pm", "報告", "impl")
        self.assertEqual(inbox.entries(self.shipdir, "pm")[-1]["text"], "報告")


# --- spin, the captain gap, orphans ---------------------------------------------------

class SpinTest(_Base):
    def test_too_many_sends_warn_and_record_but_deliver(self):
        for i in range(6):
            out = self.run_cmd(seat.send, self.shipdir, "impl", f"m{i}", "pm")
            self.assertNotIn("送りすぎ", out)
        out = self.run_cmd(seat.send, self.shipdir, "impl", "m6", "pm")
        self.assertIn("送りすぎ", out)
        self.assertEqual(len(inbox.entries(self.shipdir, "impl")), 7)
        [e] = self.kinds(events.SPIN_SUSPECTED)
        self.assertEqual((e["by"], e["seat"], e["data"]["count"]), ("pm", "impl", 7))
        self.run_cmd(seat.send, self.shipdir, "pm", "x", "impl")   # another pair is counted apart
        self.assertEqual(len(self.kinds(events.SPIN_SUSPECTED)), 1)

    def test_same_text_twice_in_a_row(self):
        self.run_cmd(seat.send, self.shipdir, "impl", "同じ", "pm")
        self.run_cmd(seat.send, self.shipdir, "impl", "別", "pm")
        self.assertEqual(self.kinds(events.DUPLICATE_SUSPECTED), [])
        out = self.run_cmd(seat.send, self.shipdir, "impl", "別", "pm")
        self.assertIn("重複", out)
        self.assertEqual(len(self.kinds(events.DUPLICATE_SUSPECTED)), 1)
        self.assertEqual(len(inbox.entries(self.shipdir, "impl")), 3)

    def test_spin_off(self):
        ty = self.shipdir / "team.yaml"
        text = ty.read_text()
        start = text.index("  spin:")
        end = text.index("\n\n", start)
        ty.write_text(text[:start] + "  spin: off" + text[end:])
        seat.prepare(self.shipdir)
        for _ in range(8):
            self.run_cmd(seat.send, self.shipdir, "impl", "同じ", "pm")
        self.assertEqual(self.kinds((events.SPIN_SUSPECTED, events.DUPLICATE_SUSPECTED)), [])

    def test_daily_report_lists_the_loops(self):
        for _ in range(8):
            self.run_cmd(seat.send, self.shipdir, "impl", "同じ", "pm")
        lines = report.anomaly_lines(self.shipdir, self.team(), 0, time.time() + 1, live=False)
        [line] = [x for x in lines if "空回り" in x]
        self.assertIn("pm → impl", line)
        self.assertIn("送りすぎ 2 回", line)
        self.assertIn("同じ本文の連続 7 回", line)


class CaptainGapTest(_Base):
    def test_gap_is_recorded_once_per_stop(self):
        self.up()
        self.end_shift("pm", ago=1900)
        out = self.run_cmd(seat.send, self.shipdir, "owner", "報告", "impl")
        self.assertIn("captain pm が止まってから", out)
        self.run_cmd(seat.send, self.shipdir, "owner", "報告 2", "impl")
        [e] = self.kinds(events.CAPTAIN_GAP)
        self.assertEqual(e["seat"], "pm")
        lines = report.anomaly_lines(self.shipdir, self.team(), 0, time.time() + 1, live=False)
        self.assertTrue(any("captain pm が止まってから" in x for x in lines))

    def test_seat_stop_looks_too_and_short_gaps_are_quiet(self):
        self.up()
        self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm")
        self.end_shift("pm", ago=600)
        rec = roster.seat(self.shipdir, "impl")
        (self.shipdir / "seats" / "impl" / "handoff.md").write_text("done")
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": rec["sessionId"]}):
            self.run_cmd(seat.seat_stop, self.shipdir, "impl", 10, True)
        self.assertEqual(self.kinds(events.CAPTAIN_GAP), [])
        roster.update(self.shipdir, "pm", endedAt=time.time() - 3600)
        roster.update(self.shipdir, "impl", state=roster.ON_SHIFT)
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": rec["sessionId"]}):
            out = self.run_cmd(seat.seat_stop, self.shipdir, "impl", 10, True)
        self.assertIn("captain pm が止まってから", out)

    def test_not_while_the_ship_is_down(self):
        self.up()
        self.end_shift("pm", ago=1900)
        deadline.end_now(self.shipdir)
        self.run_cmd(seat.send, self.shipdir, "owner", "x", "impl")
        self.assertEqual(self.kinds(events.CAPTAIN_GAP), [])


class OrphanTest(_Base):
    def test_captain_injection_lists_orphans(self):
        t = self.team()
        brd = board.Board(self.shipdir, t)
        a = brd.add("落ちた", {"assignee": "impl", "state": "active"})
        brd.add("まだ動いている", {"assignee": "impl", "state": "open"})
        roster.start_shift(self.shipdir, "impl", session_id="s" * 36, short_id="s" * 8, session_name="t1.impl",
                           how="new")
        roster.end_shift(self.shipdir, "impl", reason="exited", handoff_written=False, note=roster.NO_HANDOFF_NOTE)
        text, _ = inject.build(self.shipdir, t, "pm")
        self.assertIn("## 孤児の項目", text)
        self.assertIn(f"{a['id']} [active] impl: 落ちた ← impl の最後のシフト #1 が引き継ぎなしで終了", text)
        self.assertNotIn("まだ動いている ←", text)
        self.assertNotIn("孤児", inject.build(self.shipdir, t, "impl")[0])   # the captain's part

    def test_stopped_long_enough_with_a_handoff(self):
        t = self.team()
        a = board.Board(self.shipdir, t).add("止まっている", {"assignee": "impl", "state": "active"})
        roster.start_shift(self.shipdir, "impl", session_id="s" * 36, short_id="s" * 8, session_name="t1.impl",
                           how="new")
        roster.end_shift(self.shipdir, "impl", reason="seat-stop", handoff_written=True, now=time.time() - 600)
        self.assertIn("(なし)", inject.build(self.shipdir, t, "pm")[0].split("## 孤児の項目")[1][:40])
        roster.update(self.shipdir, "impl", endedAt=time.time() - 2000)
        self.assertIn(f"{a['id']} [active] impl: 止まっている ← impl が止まってから 33m",
                      inject.build(self.shipdir, t, "pm")[0])


# --- send --cwd (design-p1 §8.2 の 2) ------------------------------------------------------

class SendCwdTest(_Base):
    def setUp(self):
        super().setUp()
        self.wt = self.tmp / "wt"
        self.wt.mkdir()
        cfg = json.loads((self.config / ".claude.json").read_text())
        cfg["projects"][str(self.wt)] = {"hasTrustDialogAccepted": True}
        (self.config / ".claude.json").write_text(json.dumps(cfg))
        self.up()

    def test_per_task_seat_starts_in_the_directory_without_isolation(self):
        out = self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm", cwd=str(self.wt))
        call = self.bg_calls()[-1]
        self.assertEqual(call["cwd"], str(self.wt))
        a = call["argv"]
        settings = json.loads(Path(a[a.index("--settings") + 1]).read_text())
        self.assertEqual(settings["worktree"]["bgIsolation"], "none")
        self.assertIn("hooks", settings)
        rec = roster.seat(self.shipdir, "impl")
        self.assertEqual(rec["cwd"], str(self.wt))
        self.assertIsNone(rec["nextCwd"])
        self.assertIn(f"{self.wt} を cwd にして", out)
        self.stop_session("impl")
        self.run_cmd(seat.send, self.shipdir, "impl", "T-002", "pm")   # used up: back to the workspace
        self.assertEqual(self.bg_calls()[-1]["cwd"], str(self.workspace))

    def test_alive_seat_takes_it_on_the_next_shift(self):
        self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm")
        out = self.run_cmd(seat.send, self.shipdir, "impl", "T-002", "pm", cwd=str(self.wt))
        self.assertIn("次のシフトから効く", out)
        self.stop_session("impl")
        self.run_cmd(seat.send, self.shipdir, "impl", "T-003", "pm")
        self.assertEqual(self.bg_calls()[-1]["cwd"], str(self.wt))

    def test_refused_for_persistent_owner_missing_or_untrusted(self):
        with self.assertRaises(YamatoError):
            self.run_cmd(seat.send, self.shipdir, "pm", "x", "owner", cwd=str(self.wt))
        with self.assertRaises(YamatoError):
            self.run_cmd(seat.send, self.shipdir, "owner", "x", "pm", cwd=str(self.wt))
        with self.assertRaises(YamatoError):
            self.run_cmd(seat.send, self.shipdir, "impl", "x", "pm", cwd=str(self.tmp / "nope"))
        other = self.tmp / "other"
        other.mkdir()
        with self.assertRaises(YamatoError) as cm:
            self.run_cmd(seat.send, self.shipdir, "impl", "x", "pm", cwd=str(other))
        self.assertIn("trust", str(cm.exception))

    def test_unknown_trust_warns_and_leaves_it_to_claude(self):
        (self.config / ".claude.json").write_text("{broken")
        out = self.run_cmd(seat.send, self.shipdir, "impl", "T-001", "pm", cwd=str(self.wt))
        self.assertIn("確かめられなかった", out)
        self.assertEqual(self.bg_calls()[-1]["cwd"], str(self.wt))


class HeadlessCwdTest(ShipTestCase):
    def test_headless_shift_runs_in_the_directory(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace(
            "roles:\n", "roles:\n  researcher:\n    model: sonnet\n    shift: headless\n    description: 調査\n", 1))
        (self.shipdir / "roles" / "researcher.md").write_text("調査担当\n")
        seat.prepare(self.shipdir)
        deadline.write(self.shipdir, limit=600, grace=60, token="t")
        wt = self.tmp / "wt"
        wt.mkdir()
        with mock.patch.object(headless, "spawn"), redirect_stdout(io.StringIO()):
            seat.send(self.shipdir, "researcher", "調べて", "owner", str(wt))
        self.assertEqual(roster.seat(self.shipdir, "researcher")["nextCwd"], str(wt))
        with mock.patch.object(seat, "wake", return_value=("alive", {})):
            headless.run(self.shipdir, "researcher")
        [call] = [c for c in self.fake()["calls"] if "-p" in c["argv"]]
        self.assertEqual(call["cwd"], str(wt))
        self.assertIsNone(roster.seat(self.shipdir, "researcher")["nextCwd"])


if __name__ == "__main__":
    unittest.main()
