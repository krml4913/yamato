import io
import json
import os
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import board, deadline, hooks, inbox, inject, pretool, roster, runtime, seat
from yamato.runtime import ship_arg
from yamato.util import YAMATO_BIN


class InjectTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        self.t = self.team()
        self.sdir = self.shipdir / "seats" / "impl"
        self.rdir = self.shipdir / "roles" / "impl"   # the role's memory (design-p1 §3)
        self.rdir.mkdir(parents=True, exist_ok=True)

    def build(self, **limits):
        return inject.build(self.shipdir, self.t, "impl", "startup", limits or None)

    @staticmethod
    def _fleet_section(text):
        """Only the `fleet` part, not the whole hook: the header carries the ship folder
        (a random tmpXXXXXXXX path) and asserting ship names like t2/t3 against the full
        text risks a ~1% collision with that random suffix (reviewer #16 T-022)."""
        start = text.index("## fleet")
        rest = text[start:]
        end = rest.find("\n## ", 1)
        return rest if end == -1 else rest[:end]

    def test_sections_present(self):
        (self.sdir / "handoff.md").write_text("前回: T-001 途中", encoding="utf-8")
        (self.rdir / "memory.md").write_text("モックは 30 日で切れる", encoding="utf-8")
        board.Board(self.shipdir, self.t).add("関数を足す", {"assignee": "impl"})
        inbox.append(self.shipdir, "impl", "pm", "T-001 を頼む")
        text, cur = self.build()
        for s in ("あなたの席: impl", "前回: T-001 途中", "T-001 [open] impl: 関数を足す",
                  "T-001 を頼む", ship_arg(self.shipdir)):
            self.assertIn(s, text)
        self.assertEqual(cur, 1)
        # the role's memory and knowledge.md are the other hook's (verify-p0-c Q1)
        self.assertNotIn("モックは 30 日", text)
        knowledge = inject.build_knowledge(self.shipdir, self.t, "impl")
        for s in ("モックは 30 日", "チームの knowledge.md", "席 impl"):
            self.assertIn(s, knowledge)
        self.assertNotIn("T-001", knowledge)

    def test_parts_follow_team_yaml(self):
        (self.sdir / "handoff.md").write_text("HANDOFF-X", encoding="utf-8")
        (self.shipdir / "knowledge.md").write_text("KNOW-X", encoding="utf-8")
        self.t["roles"]["impl"]["inject"] = ["knowledge"]
        text, _ = self.build()
        self.assertNotIn("HANDOFF-X", text)
        self.assertNotIn("未読の inbox", text)
        self.assertIn("あなたの席: impl", text)  # the header is always there
        knowledge = inject.build_knowledge(self.shipdir, self.t, "impl")
        self.assertIn("KNOW-X", knowledge)
        self.assertNotIn("## 役割の memory", knowledge)
        self.t["roles"]["impl"]["inject"] = ["handoff"]
        self.assertEqual(inject.build_knowledge(self.shipdir, self.t, "impl"), "")

    def test_limits_from_team_yaml(self):
        (self.sdir / "handoff.md").write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
        self.t["inject"]["limits"]["handoff"] = [3, 1000]
        text, _ = self.build()
        self.assertIn("line2", text)
        self.assertNotIn("line3", text)

    def test_handoff_capped_by_lines(self):
        (self.sdir / "handoff.md").write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
        text, _ = self.build(handoff=(40, 10000))
        self.assertIn("line39", text)
        self.assertNotIn("line40", text)
        self.assertIn(f"…(上限で切った。全文は `{self.sdir / 'handoff.md'}` を Read せよ)", text)

    def test_memory_and_knowledge_capped_by_memory_limits(self):
        """One set of limits: where memory apply refuses is where the injection cuts."""
        (self.rdir / "memory.md").write_text("m" * 5000, encoding="utf-8")
        (self.shipdir / "knowledge.md").write_text("k" * 6000, encoding="utf-8")
        text = inject.build_knowledge(self.shipdir, self.t, "impl")
        self.assertIn("m" * 4000 + "\n…(memory.md が上限を超えている (5000 文字 (上限 4000 文字))", text)
        self.assertNotIn("m" * 4001, text)
        self.assertIn(f"全文は `{self.rdir / 'memory.md'}` を Read せよ", text)
        self.assertIn("k" * 5000 + "\n…(knowledge.md が上限を超えている", text)
        self.assertNotIn("k" * 5001, text)
        self.assertLessEqual(len(text), inject.LIMITS["total_chars"])   # both fit the hook at the default limits

    def test_board_mine_capped(self):
        b = board.Board(self.shipdir, self.t)
        for i in range(30):
            b.add(f"task{i}", {"assignee": "impl"})
        text, _ = self.build(mine_items=5)
        self.assertIn("task4", text)
        self.assertNotIn("task5", text)
        self.assertIn("ほか 25 件", text)

    def test_board_part_is_opt_in_and_capped(self):
        """T-030: the `board` part is not in a role's inject unless named (opt-in, DEFAULT
        excludes it); when named, it lists the whole ship (not just `impl`'s own), capped
        by `board_items` and ordered blocked -> active -> open."""
        b = board.Board(self.shipdir, self.t)
        b.add("open one", {"assignee": "pm", "state": "open"})
        b.add("blocked one", {"state": "blocked"})
        b.add("done one", {"state": "done"})   # never counted or shown
        text, _ = self.build()
        self.assertNotIn("board (艦全体", text)   # not in the default parts
        self.t["roles"]["impl"]["inject"] = [*self.t["inject"]["parts"], "board"]
        text, _ = self.build(board_items=1)
        self.assertIn("## board (艦全体の進み具合)", text)
        self.assertIn("blocked 1 / active 0 / open 1", text)
        self.assertIn("T-002 [blocked]", text)   # blocked comes first...
        self.assertNotIn("T-001 [open]", text)   # ...and the open one is past the cap of 1
        self.assertIn("ほか 1 件 (`", text)
        self.assertIn("board kanban", text)
        self.assertNotIn("done one", text)

    def test_fleet_part_is_opt_in_and_lists_every_ship(self):
        """T-022: the `fleet` part (admiral's own; opt-in like `board`) is a one-line-per-ship
        summary, the same shape as `yamato ships` (admiral.ship_line), not just this ship."""
        from yamato import ship

        text, _ = self.build()
        self.assertNotIn("fleet (全艦の様子", text)   # not in the default parts
        ship.create("t2", str(self.workspace), None, "dev")
        self.t["roles"]["impl"]["inject"] = [*self.t["inject"]["parts"], "fleet"]
        text, _ = self.build()
        section = self._fleet_section(text)
        self.assertIn(f"## fleet (全艦の様子。全文は `{runtime.yamato_invocation()} ships`)", section)
        self.assertIn("t1", section)
        self.assertIn("t2", section)
        self.assertIn("停止中", section)   # neither ship was `up`

    def test_fleet_part_capped(self):
        """Past `fleet_items`, the rest is a count pointing at the uncapped `yamato ships`,
        not a line cut mid-way (完了条件 2: 艦の数が多いときの上限)."""
        from yamato import ship

        for name in ("t2", "t3"):
            ship.create(name, str(self.workspace), None, "dev")
        self.t["roles"]["impl"]["inject"] = [*self.t["inject"]["parts"], "fleet"]
        text, _ = self.build(fleet_items=1)
        section = self._fleet_section(text)
        self.assertIn("t1", section)
        self.assertNotIn("t2", section)
        self.assertNotIn("t3", section)
        self.assertIn(f"…ほか 2 件 (`{runtime.yamato_invocation()} ships` で見る)", section)

    def test_fleet_part_survives_claude_agents_failure(self):
        """reviewer #16 (T-022 差し戻し): claude.agents() は失敗/タイムアウトで YamatoError
        を投げうる。おまけの `fleet` 部品のせいで session_start 全体を落とすな (_touch と同じ
        考え) — handoff など本業の部品は出た上で、fleet だけ読めない旨に置き換わること。"""
        from yamato.util import YamatoError

        (self.sdir / "handoff.md").write_text("HANDOFF-Y", encoding="utf-8")
        self.t["roles"]["impl"]["inject"] = [*self.t["inject"]["parts"], "fleet"]
        with mock.patch("yamato.claude.agents", side_effect=YamatoError("タイムアウトしました")):
            text, _ = self.build()
        self.assertIn("HANDOFF-Y", text)   # the real job survives the extra part's failure
        self.assertIn("未読の inbox", text)
        section = self._fleet_section(text)
        self.assertIn("## fleet (全艦の様子", section)
        self.assertIn("claude agents を読めない: タイムアウトしました", section)
        self.assertIn(f"`{runtime.yamato_invocation()} ships`", section)

    def test_inbox_capped_and_cursor_only_over_full_messages(self):
        for i in range(12):
            inbox.append(self.shipdir, "impl", "pm", f"msg{i}")
        text, cur = self.build(inbox_messages=5)
        self.assertIn("msg4", text)
        self.assertNotIn("msg5", text)
        self.assertEqual(cur, 5)
        self.assertIn("未読の inbox (12 件)", text)

        inbox.mark_read(self.shipdir, "impl", 12)
        inbox.append(self.shipdir, "impl", "pm", "short")
        inbox.append(self.shipdir, "impl", "pm", "L" * 1000)
        inbox.append(self.shipdir, "impl", "pm", "after")
        text, cur = self.build(inbox_chars=100)
        self.assertEqual(cur, 13)  # stops before the truncated message
        self.assertIn("inbox", text)

    def test_total_cap(self):
        (self.sdir / "handoff.md").write_text("h" * 3000, encoding="utf-8")
        for i in range(10):
            inbox.append(self.shipdir, "impl", "pm", "x" * 390)
        (self.shipdir / "knowledge.md").write_text("k" * 3000, encoding="utf-8")
        text, cur = self.build(total_chars=3000, handoff=(40, 1500))
        self.assertLessEqual(len(text), 3000)
        shown = text.count("from pm")
        self.assertEqual(cur, shown)  # only what fit is marked read
        self.assertNotIn("k" * 100, text)   # knowledge.md is the other hook's

    def test_total_cap_keeps_the_whole_text_to_read(self):
        (self.sdir / "handoff.md").write_text("\n".join(f"h{i:04d}" for i in range(1000)), encoding="utf-8")
        text, _ = self.build(total_chars=500, handoff=(1000, 100000))
        full = self.shipdir / ".runtime" / "inject-impl-records.md"
        self.assertLessEqual(len(text), 500)
        self.assertTrue(text.endswith(f"…(注入の上限 500 文字で切った。全文は `{full}` を Read せよ)"))
        self.assertIn("h0999", full.read_text(encoding="utf-8"))

    def test_no_handoff_shift_shows_log_tail(self):
        roster.start_shift(self.shipdir, "impl", session_id="a" * 36, short_id="aaaaaaaa",
                           session_name="t1.impl", how="new")
        roster.start_shift(self.shipdir, "impl", session_id="b" * 36, short_id="bbbbbbbb",
                           session_name="t1.impl", how="new")
        (self.sdir / "log" / "2026-01-01.md").write_text("\n".join(f"- ev{i}" for i in range(50)), encoding="utf-8")
        text, _ = self.build()
        self.assertIn("引き継ぎなしで終わった", text)
        self.assertIn("ev49", text)
        self.assertNotIn("ev10\n", text)


class HookTest(ShipTestCase):
    def run_hook(self, fn, stdin: dict):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(stdin))), redirect_stdout(out), redirect_stderr(err):
            code = fn(self.shipdir, "impl")
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        super().setUp()
        runtime.generate(self.shipdir, self.team())
        roster.start_shift(self.shipdir, "impl", session_id="s" * 36, short_id="ssssssss",
                           session_name="t1.impl", how="new")

    def test_stop_and_pre_tool_use_are_quiet_with_no_deadline_at_all(self):
        # T-020 / D-013: the admiral's permanent state is no ``.runtime/deadline`` ever
        # written (``time_limit: none``), so ``deadline.phase`` stays NOT_UP forever and
        # none of §0 B4's wrap-up / force-stop fires, the same as before any ``up``.
        self.assertIsNone(deadline.read(self.shipdir))
        self.assertEqual(self.run_hook(hooks.stop, {})[1], "")
        self.assertEqual(self.run_hook(hooks.pre_tool_use, {"tool_name": "Bash"}), (0, "", ""))
        self.assertEqual(seat.enforce(self.shipdir, self.team()), [])

    def test_session_start_emits_context_and_marks_read(self):
        inbox.append(self.shipdir, "impl", "pm", "hello")
        code, out, _ = self.run_hook(hooks.session_start, {"source": "compact", "session_id": "x"})
        self.assertEqual(code, 0)
        data = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(data["hookEventName"], "SessionStart")
        self.assertIn("(compact)", data["additionalContext"])
        self.assertIn("hello", data["additionalContext"])
        self.assertEqual(inbox.unread(self.shipdir, "impl"), [])

    def test_session_start_knowledge_is_its_own_hook(self):
        (self.shipdir / "knowledge.md").write_text("KNOW-X", encoding="utf-8")
        code, out, _ = self.run_hook(hooks.session_start, {})
        self.assertNotIn("KNOW-X", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        code, out, _ = self.run_hook(hooks.session_start_knowledge, {})
        self.assertEqual(code, 0)
        data = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(data["hookEventName"], "SessionStart")
        self.assertIn("KNOW-X", data["additionalContext"])
        # two hooks with different commands (one command twice would be merged into one)
        settings = json.loads(runtime.settings_path(self.shipdir, "impl").read_text(encoding="utf-8"))
        cmds = [(h["command"], tuple(h["args"])) for g in settings["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertEqual(len(set(cmds)), 2)
        self.assertTrue(any("session-start-knowledge" in args for _, args in cmds))

    def test_stop_hook_quiet_before_deadline(self):
        deadline.write(self.shipdir, limit=600, grace=60, token="t")
        self.assertEqual(self.run_hook(hooks.stop, {})[1], "")

    def test_stop_hook_blocks_past_deadline_a_bounded_number_of_times(self):
        deadline.write(self.shipdir, limit=0, grace=600, token="t", now=time.time() - 1)
        outs = [self.run_hook(hooks.stop, {"stop_hook_active": False})[1] for _ in range(5)]
        blocked = [json.loads(o) for o in outs if o]
        self.assertEqual(len(blocked), hooks.MAX_WRAPUP_NOTICES)
        self.assertEqual(blocked[0]["decision"], "block")
        self.assertIn("seat-stop", blocked[0]["reason"])

    def test_stop_hook_respects_stop_hook_active_and_stopping(self):
        deadline.write(self.shipdir, limit=0, grace=600, token="t", now=time.time() - 1)
        self.assertEqual(self.run_hook(hooks.stop, {"stop_hook_active": True})[1], "")
        roster.mark_stopping(self.shipdir, "impl", handoff_written=True)
        self.assertEqual(self.run_hook(hooks.stop, {})[1], "")

    def test_wait_deadline_wakes_with_exit_2(self):
        deadline.write(self.shipdir, limit=0, grace=600, token="t", now=time.time() - 1)
        code, _, err = self.run_hook(hooks.wait_deadline, {})
        self.assertEqual(code, 2)
        self.assertIn("seat-stop", err)
        self.assertFalse((self.shipdir / ".runtime" / "wait-impl.pid").exists())

    def test_wait_deadline_single_watcher(self):
        deadline.write(self.shipdir, limit=0, grace=600, token="t", now=time.time() - 1)
        pidfile = self.shipdir / ".runtime" / "wait-impl.pid"
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        pidfile.write_text(f"{sleeper.pid}\n", encoding="utf-8")   # 別の生きたプロセス (pid 1 は Windows に無い)
        self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 0)

    def test_wait_deadline_exits_when_not_up(self):
        self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 0)

    # --- T-021: time_limit: none (D-013, the admiral) never has a deadline to read at all,
    # not just "not up yet" -- the watcher must still notice an outside sender's message ---

    def _make_no_time_limit(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text(encoding="utf-8").replace("time_limit: 3h", "time_limit: none"), encoding="utf-8")
        runtime.generate(self.shipdir, self.team())

    def test_wait_deadline_wakes_for_inbox_with_no_deadline_at_all(self):
        self._make_no_time_limit()
        self.assertIsNone(deadline.read(self.shipdir))
        inbox.append(self.shipdir, "impl", "owner", "hello")
        code, _, err = self.run_hook(hooks.wait_deadline, {})
        self.assertEqual(code, 2)
        self.assertIn("1 件、owner から", err)

    def test_wait_deadline_without_a_deadline_keeps_polling_when_the_inbox_is_quiet(self):
        self._make_no_time_limit()

        def stop_seat(_):
            roster.mark_stopping(self.shipdir, "impl", handoff_written=True)

        with mock.patch.object(hooks.time, "sleep", side_effect=stop_seat) as sleep:
            self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 0)
        sleep.assert_called_once_with(hooks.WAIT_POLL)

    # --- T-038: a watcher with no deadline leaves when it is stale ---

    def _stop_after(self, n, fn=None):
        """A ``time.sleep`` stand-in: runs ``fn`` on the n-th call, and ends the shift on the one after
        (so a watcher that wrongly stays cannot loop forever)."""
        calls = []

        def sleep(_):
            calls.append(1)
            if fn and len(calls) == n:
                fn()
            elif len(calls) > n + 1:
                roster.mark_stopping(self.shipdir, "impl", handoff_written=True)
        return sleep, calls

    def test_wait_deadline_leaves_when_the_shift_moved_on(self):
        self._make_no_time_limit()

        def next_shift():
            roster.start_shift(self.shipdir, "impl", session_id="n" * 36, short_id="nnnnnnnn",
                               session_name="t2.impl", how="new")

        sleep, calls = self._stop_after(1, next_shift)
        with mock.patch.object(hooks.time, "sleep", side_effect=sleep):
            self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 0)
        self.assertEqual(len(calls), 1)   # left at the poll right after the shift changed

    def test_wait_deadline_leaves_when_the_pidfile_names_another_watcher(self):
        self._make_no_time_limit()
        pidfile = self.shipdir / ".runtime" / "wait-impl.pid"
        sleep, calls = self._stop_after(1, lambda: pidfile.write_text("1 1\n", encoding="utf-8"))
        with mock.patch.object(hooks.time, "sleep", side_effect=sleep):
            self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(pidfile.read_text(encoding="utf-8"), "1 1\n")   # the other watcher's file is not ours to remove

    def test_wait_deadline_takes_over_from_a_live_watcher_of_an_older_shift(self):
        self._make_no_time_limit()
        pidfile = self.shipdir / ".runtime" / "wait-impl.pid"
        pidfile.write_text("1 0\n", encoding="utf-8")   # launchd: always alive, but for shift 0
        inbox.append(self.shipdir, "impl", "owner", "hello")
        self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 2)

    def test_wait_deadline_leaves_when_its_session_is_gone(self):
        self._make_no_time_limit()
        sleep, calls = self._stop_after(99)
        with mock.patch.object(hooks, "LIVENESS_EVERY", 3), \
                mock.patch.object(hooks.time, "sleep", side_effect=sleep), \
                mock.patch("yamato.claude.find", return_value=None) as find:
            code, _, _ = self.run_hook(hooks.wait_deadline, {"session_id": "z" * 36})
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)   # polls 1, 2 quiet; the 3rd asked and left
        find.assert_called_once_with("z" * 36)

    def test_wait_deadline_stays_while_its_session_lives_or_the_answer_is_unknown(self):
        self._make_no_time_limit()
        for find in (mock.Mock(return_value={"pid": os.getpid()}), mock.Mock(side_effect=hooks.YamatoError("boom"))):
            roster.start_shift(self.shipdir, "impl", session_id="s" * 36, short_id="ssssssss",
                               session_name="t1.impl", how="resume")
            sleep, calls = self._stop_after(7)
            with mock.patch.object(hooks, "LIVENESS_EVERY", 3), \
                    mock.patch.object(hooks.time, "sleep", side_effect=sleep), \
                    mock.patch("yamato.claude.find", find):
                self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 0)
            self.assertGreater(len(calls), 7)   # only the shift ending made it leave
            self.assertGreaterEqual(find.call_count, 2)

    def test_wait_deadline_never_asks_claude_when_there_is_a_deadline(self):
        deadline.write(self.shipdir, limit=600, grace=60, token="t")
        sleep, calls = self._stop_after(5)
        with mock.patch.object(hooks, "LIVENESS_EVERY", 1), \
                mock.patch.object(hooks.time, "sleep", side_effect=sleep), \
                mock.patch("yamato.claude.find") as find:
            self.run_hook(hooks.wait_deadline, {})
        find.assert_not_called()

    def test_wait_deadline_takes_a_team_json_without_time_limit_as_a_normal_ship(self):
        # not "none": an older / hand-edited runtime team.json has no key at all
        path = self.shipdir / ".runtime" / "team.json"
        team = json.loads(path.read_text(encoding="utf-8"))
        team.pop("time_limit", None)
        path.write_text(json.dumps(team), encoding="utf-8")
        self.assertIsNone(deadline.read(self.shipdir))
        inbox.append(self.shipdir, "impl", "owner", "hello")
        self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 0)   # no deadline yet → returns, as before ``up``

    def test_wait_deadline_wakes_for_inbox_from_outside_the_seats(self):
        # e2e-p1 C: the owner's / yamato's entries have no sender to SendMessage them
        deadline.write(self.shipdir, limit=600, grace=60, token="t")
        inbox.append(self.shipdir, "impl", "yamato", "researcher のシフトが終了")
        code, _, err = self.run_hook(hooks.wait_deadline, {})
        self.assertEqual(code, 2)
        self.assertIn("inbox に未読があります (1 件、yamato から)", err)

    def test_wait_deadline_inbox_wakes_once_and_skips_seat_senders(self):
        deadline.write(self.shipdir, limit=600, grace=60, token="t")
        inbox.append(self.shipdir, "impl", "owner", "D-001 が決まった")
        self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 2)
        # not read yet, but already woken for; a seat sender SendMessages itself (§0 B1)
        inbox.append(self.shipdir, "impl", "pm", "T-002 も頼む")

        def stop_seat(_):
            roster.mark_stopping(self.shipdir, "impl", handoff_written=True)

        with mock.patch.object(hooks.time, "sleep", side_effect=stop_seat) as sleep:
            self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 0)
        sleep.assert_called_once()
        inbox.append(self.shipdir, "impl", "owner", "もう一件")
        roster.start_shift(self.shipdir, "impl", session_id="s" * 36, short_id="ssssssss",
                           session_name="t1.impl", how="resume")
        code, _, err = self.run_hook(hooks.wait_deadline, {})
        self.assertEqual(code, 2)
        self.assertIn("1 件、owner から", err)

    def test_wait_deadline_no_inbox_wake_past_deadline(self):
        deadline.write(self.shipdir, limit=0, grace=600, token="t", now=time.time() - 1)
        inbox.append(self.shipdir, "impl", "owner", "hello")
        code, _, err = self.run_hook(hooks.wait_deadline, {})
        self.assertIn("seat-stop", err)
        self.assertNotIn("未読", err)

    # --- #9: take_inbox_wake skips the ship lock when inbox.jsonl has not changed ---

    def _spy_lock(self):
        from yamato import util

        seen = []
        real = util.ship_lock

        def spy(shipdir):
            seen.append(shipdir)
            return real(shipdir)

        return seen, mock.patch.object(hooks, "ship_lock", spy)

    def test_take_inbox_wake_skips_the_lock_when_unchanged(self):
        seats = {"pm", "impl"}
        inbox.append(self.shipdir, "impl", "owner", "1件目")
        seen, patch = self._spy_lock()
        with patch:
            news = hooks.take_inbox_wake(self.shipdir, "impl", seats)
            self.assertEqual([e["text"] for e in news], ["1件目"])
            self.assertEqual(len(seen), 1)   # 変化があった: ロックを取った
            # 何も変わっていない2回目: ロックを取らずに空を返す
            self.assertEqual(hooks.take_inbox_wake(self.shipdir, "impl", seats), [])
            self.assertEqual(len(seen), 1)

    def test_take_inbox_wake_reads_only_the_new_tail_after_a_change(self):
        seats = {"pm", "impl"}
        inbox.append(self.shipdir, "impl", "owner", "1件目")
        first = hooks.take_inbox_wake(self.shipdir, "impl", seats)
        self.assertEqual(len(first), 1)
        ipath = inbox.path(self.shipdir, "impl")
        offset_after_first = json.loads((self.shipdir / ".runtime" / "inbox-wake-impl.json").read_text(encoding="utf-8"))["offset"]
        self.assertEqual(offset_after_first, ipath.stat().st_size)
        inbox.append(self.shipdir, "impl", "owner", "2件目")
        # 前回までの範囲をもう一度パースしていないことを確かめる: 追記後のバイトだけ読む
        real_open = open
        seen_offsets = []

        def spying_open(path, *a, **k):
            f = real_open(path, *a, **k)
            if str(path) == str(ipath) and a[:1] == ("r",):
                real_seek = f.seek

                def seek(pos, *sa):
                    seen_offsets.append(pos)
                    return real_seek(pos, *sa)
                f.seek = seek
            return f

        with mock.patch("builtins.open", spying_open):
            second = hooks.take_inbox_wake(self.shipdir, "impl", seats)
        self.assertEqual([e["text"] for e in second], ["2件目"])
        self.assertEqual(seen_offsets, [offset_after_first])   # 先頭からではなく前回の続きから

    def test_take_inbox_wake_drops_pending_once_the_seat_reads_it(self):
        # a message that arrives while another seat sender's news is pending stays
        # cached until read; once mark_read passes it, the cache does not grow forever
        seats = {"pm", "impl"}
        inbox.append(self.shipdir, "impl", "owner", "1件目")
        hooks.take_inbox_wake(self.shipdir, "impl", seats)
        inbox.mark_read(self.shipdir, "impl", 1)
        inbox.append(self.shipdir, "impl", "owner", "2件目")
        hooks.take_inbox_wake(self.shipdir, "impl", seats)
        pending = json.loads((self.shipdir / ".runtime" / "inbox-wake-impl.json").read_text(encoding="utf-8"))["pending"]
        self.assertEqual([e["n"] for e in pending], [2])   # 読まれた #1 はキャッシュから落ちている

    # --- the time limit inside a long turn (§0 B4) ---

    def past(self, grace_left: float):
        """Deadline passed; ``grace_left`` seconds of grace left (negative: past the grace too)."""
        deadline.write(self.shipdir, limit=0, grace=600, token="t", now=time.time() - 600 + grace_left)

    def test_pre_tool_use_quiet_while_running(self):
        deadline.write(self.shipdir, limit=600, grace=60, token="t")
        self.assertEqual(self.run_hook(hooks.pre_tool_use, {"tool_name": "Bash"}), (0, "", ""))
        self.assertEqual(pretool.main(str(self.shipdir), "impl"), 0)
        self.assertEqual(pretool.main(str(self.tmp / "no-ship"), "impl"), 0)   # not up

    def test_pre_tool_use_wrapup_shares_the_count_with_stop(self):
        self.past(300)
        code, out, _ = self.run_hook(hooks.pre_tool_use, {"tool_name": "Bash"})
        d = json.loads(out)["hookSpecificOutput"]
        self.assertEqual((code, d["hookEventName"]), (0, "PreToolUse"))
        self.assertNotIn("permissionDecision", d)   # the tool still runs: the seat has to wrap up
        self.assertIn("seat-stop", d["additionalContext"])
        # a second tool call in the SAME turn (no Stop hook in between) does not repeat it
        # (design-drift nit B): the shared budget's first slot is spent once, not per call
        self.assertEqual(self.run_hook(hooks.pre_tool_use, {})[1], "")
        # the turn ends without the seat actually stopping: Stop spends the 2nd slot
        self.assertTrue(self.run_hook(hooks.stop, {})[1])
        # a new turn's first PreToolUse call notifies again: the third and last slot
        self.assertTrue(self.run_hook(hooks.pre_tool_use, {})[1])
        # further calls in that same turn, and the turn's own Stop, find the budget spent
        self.assertEqual(self.run_hook(hooks.pre_tool_use, {})[1], "")
        self.assertEqual(self.run_hook(hooks.stop, {})[1], "")

    def test_pre_tool_use_does_not_repeat_within_the_same_turn(self):
        # same condition as above, isolated: several tool calls in one turn (the seat's own
        # wrap-up -- Write the handoff, then run other commands) must not spend more than
        # one slot of the shared budget before the turn's Stop hook runs
        self.past(300)
        first = self.run_hook(hooks.pre_tool_use, {"tool_name": "Write"})[1]
        self.assertIn("seat-stop", first)
        for _ in range(3):   # still the same turn: no Stop hook has run yet
            self.assertEqual(self.run_hook(hooks.pre_tool_use, {"tool_name": "Bash"})[1], "")
        # the turn ends without the seat actually stopping: the shared budget's 2nd slot
        self.assertTrue(self.run_hook(hooks.stop, {})[1])
        # a new turn's first call gets the third and last slot
        self.assertTrue(self.run_hook(hooks.pre_tool_use, {"tool_name": "Bash"})[1])
        # the whole shift's budget is spent now, whatever calls follow
        self.assertEqual(self.run_hook(hooks.pre_tool_use, {})[1], "")
        self.assertEqual(self.run_hook(hooks.stop, {})[1], "")

    def test_pre_tool_use_is_quiet_once_roster_shows_stopping(self):
        # the seat starts wrapping up: Write(handoff.md) still gets the (first) notice...
        self.past(300)
        out = self.run_hook(hooks.pre_tool_use, {"tool_name": "Write"})[1]
        self.assertIn("seat-stop", out)
        # ...but once yamato seat-stop has been accepted (roster shows STOPPING), further
        # tool calls in the wrap-up sequence get nothing more, even with budget still left
        roster.mark_stopping(self.shipdir, "impl", handoff_written=True)
        self.assertEqual(self.run_hook(hooks.pre_tool_use, {"tool_name": "Bash"})[1], "")
        self.assertEqual(self.run_hook(hooks.stop, {})[1], "")

    def test_pre_tool_use_denies_and_stops_the_seat_once_past_the_grace(self):
        self.past(-1)
        with mock.patch.object(seat, "spawn_delayed_stop") as spawn:
            outs = [self.run_hook(hooks.pre_tool_use, {"session_id": "s" * 36, "tool_name": "Bash"})[1]
                    for _ in range(3)]
            spawn.assert_called_once_with(self.shipdir, "impl", "s" * 36, hooks.FORCE_STOP_AFTER, forced=True)
            for o in outs:
                d = json.loads(o)["hookSpecificOutput"]
                self.assertEqual(d["permissionDecision"], "deny")
                self.assertIn("止めます", d["permissionDecisionReason"])
            # the fast path hands over past the deadline
            with redirect_stdout(io.StringIO()) as buf, mock.patch("sys.stdin", io.StringIO("{}")):
                self.assertEqual(pretool.main(str(self.shipdir), "impl"), 0)
            self.assertIn('"deny"', buf.getvalue())
            # another session of the seat gets its own stop
            self.run_hook(hooks.pre_tool_use, {"session_id": "t" * 36})
            self.assertEqual(spawn.call_count, 2)
            # a persistent seat resumes under the same session id: the next shift is stopped too
            roster.start_shift(self.shipdir, "impl", session_id="t" * 36, short_id="tttttttt",
                               session_name="t1.impl", how="resume")
            self.run_hook(hooks.pre_tool_use, {"session_id": "t" * 36})
            self.assertEqual(spawn.call_count, 3)
            # a stop that did not take is tried again
            self.run_hook(hooks.pre_tool_use, {"session_id": "t" * 36})
            self.assertEqual(spawn.call_count, 3)
            with mock.patch.object(hooks.time, "time", return_value=time.time() + hooks.FORCE_STOP_RETRY):
                self.run_hook(hooks.pre_tool_use, {"session_id": "t" * 36})
        self.assertEqual(spawn.call_count, 4)
        self.assertIn("強制停止", next((self.shipdir / "seats/impl/log").glob("*.md")).read_text(encoding="utf-8"))

    def test_pre_tool_use_denies_even_if_the_stop_cannot_be_set(self):
        self.past(-1)
        with mock.patch.object(seat, "spawn_delayed_stop", side_effect=OSError("no sh")):
            code, out, err = self.run_hook(hooks.pre_tool_use, {"session_id": "s" * 36})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("no sh", err)

    def test_headless_seat_past_the_grace_is_left_to_its_wrapper(self):
        self.past(-1)
        team = self.team()
        team["seats"]["impl"]["shift"] = "headless"
        with mock.patch.object(hooks, "runtime_team", return_value=team), \
                mock.patch.object(seat, "spawn_delayed_stop") as spawn:
            out = self.run_hook(hooks.pre_tool_use, {"session_id": "s" * 36})[1]
            self.assertEqual(self.run_hook(hooks.stop, {"session_id": "s" * 36})[1], "")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        spawn.assert_not_called()

    def test_stop_hook_past_the_grace_stops_instead_of_blocking(self):
        self.past(-1)
        with mock.patch.object(seat, "spawn_delayed_stop") as spawn:
            self.assertEqual(self.run_hook(hooks.stop, {"session_id": "s" * 36, "stop_hook_active": True})[1], "")
        spawn.assert_called_once()

    def test_wait_deadline_stops_an_idle_seat_past_the_grace(self):
        self.past(-1)
        with mock.patch.object(seat, "spawn_delayed_stop") as spawn:
            self.assertEqual(self.run_hook(hooks.wait_deadline, {"session_id": "s" * 36})[0], 0)
        spawn.assert_called_once()

    def test_wait_deadline_keeps_watching_after_the_notices_run_out(self):
        self.past(300)
        for _ in range(hooks.MAX_WRAPUP_NOTICES):
            hooks.take_wrapup_notice(self.shipdir, "impl", roster.seat(self.shipdir, "impl")["shiftNo"])

        def grace_ends(_):
            self.past(-1)

        with mock.patch.object(hooks.time, "sleep", side_effect=grace_ends) as sleep, \
                mock.patch.object(seat, "spawn_delayed_stop") as spawn:
            self.assertEqual(self.run_hook(hooks.wait_deadline, {"session_id": "s" * 36})[0], 0)
        sleep.assert_called_once()
        spawn.assert_called_once()

    def test_pre_tool_use_hook_is_wired_and_skips_the_cli(self):
        settings = json.loads(runtime.settings_path(self.shipdir, "impl").read_text(encoding="utf-8"))
        [h] = [h for g in settings["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(h["args"][1:3], ["hook", "pre-tool-use"])
        deadline.write(self.shipdir, limit=600, grace=60, token="t")
        cp = subprocess.run([sys.executable, "-X", "importtime", str(YAMATO_BIN), "hook", "pre-tool-use",
                             str(self.shipdir), "impl"], capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
        self.assertEqual((cp.returncode, cp.stdout), (0, ""))
        loaded = [ln.rsplit("|", 1)[-1].strip() for ln in cp.stderr.splitlines() if "yamato" in ln]
        self.assertEqual(loaded, ["yamato", "yamato.pretool"])

    def test_deny_dialog(self):
        code, out, _ = self.run_hook(hooks.deny_dialog, {"tool_name": "Write", "tool_input": {"file_path": "x"}})
        d = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(d["decision"]["behavior"], "deny")
        log = next((self.shipdir / "seats/impl/log").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("自動で拒否: Write", log)


if __name__ == "__main__":
    unittest.main()
