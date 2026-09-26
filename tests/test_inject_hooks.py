import io
import json
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import board, deadline, hooks, inbox, inject, roster, runtime


class InjectTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        self.t = self.team()
        self.sdir = self.shipdir / "seats" / "impl"

    def build(self, **limits):
        return inject.build(self.shipdir, self.t, "impl", "startup", limits or None)

    def test_sections_present(self):
        (self.sdir / "handoff.md").write_text("前回: T-001 途中")
        (self.sdir / "memory.md").write_text("モックは 30 日で切れる")
        board.Board(self.shipdir, self.t).add("関数を足す", {"assignee": "impl"})
        inbox.append(self.shipdir, "impl", "pm", "T-001 を頼む")
        text, cur = self.build()
        for s in ("あなたの席: impl", "前回: T-001 途中", "モックは 30 日", "T-001 [open] impl: 関数を足す",
                  "T-001 を頼む", "knowledge", str(self.shipdir)):
            self.assertIn(s, text)
        self.assertEqual(cur, 1)

    def test_parts_follow_team_yaml(self):
        (self.sdir / "handoff.md").write_text("HANDOFF-X")
        (self.shipdir / "knowledge.md").write_text("KNOW-X")
        self.t["roles"]["impl"]["inject"] = ["knowledge"]
        text, _ = self.build()
        self.assertIn("KNOW-X", text)
        self.assertNotIn("HANDOFF-X", text)
        self.assertNotIn("未読の inbox", text)
        self.assertIn("あなたの席: impl", text)  # the header is always there

    def test_limits_from_team_yaml(self):
        (self.sdir / "handoff.md").write_text("\n".join(f"line{i}" for i in range(100)))
        self.t["inject"]["limits"]["handoff"] = [3, 1000]
        text, _ = self.build()
        self.assertIn("line2", text)
        self.assertNotIn("line3", text)

    def test_handoff_capped_by_lines(self):
        (self.sdir / "handoff.md").write_text("\n".join(f"line{i}" for i in range(100)))
        text, _ = self.build(handoff=(40, 10000))
        self.assertIn("line39", text)
        self.assertNotIn("line40", text)
        self.assertIn("上限で省略", text)

    def test_memory_and_knowledge_capped_by_chars(self):
        (self.sdir / "memory.md").write_text("m" * 5000)
        (self.shipdir / "knowledge.md").write_text("k" * 5000)
        text, _ = self.build(memory=(40, 100), knowledge=(60, 200))
        self.assertIn("m" * 100 + "\n…(上限で省略", text)
        self.assertNotIn("m" * 101, text)
        self.assertIn("k" * 200 + "\n…(上限で省略", text)
        self.assertNotIn("k" * 201, text)

    def test_board_mine_capped(self):
        b = board.Board(self.shipdir, self.t)
        for i in range(30):
            b.add(f"task{i}", {"assignee": "impl"})
        text, _ = self.build(mine_items=5)
        self.assertIn("task4", text)
        self.assertNotIn("task5", text)
        self.assertIn("ほか 25 件", text)

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
        (self.sdir / "handoff.md").write_text("h" * 3000)
        for i in range(10):
            inbox.append(self.shipdir, "impl", "pm", "x" * 390)
        (self.shipdir / "knowledge.md").write_text("k" * 3000)
        text, cur = self.build(total_chars=3000, handoff=(40, 1500))
        self.assertLessEqual(len(text), 3000 + 50)
        shown = text.count("from pm")
        self.assertEqual(cur, shown)  # only what fit is marked read

    def test_no_handoff_shift_shows_log_tail(self):
        roster.start_shift(self.shipdir, "impl", session_id="a" * 36, short_id="aaaaaaaa",
                           session_name="t1.impl", how="new")
        roster.start_shift(self.shipdir, "impl", session_id="b" * 36, short_id="bbbbbbbb",
                           session_name="t1.impl", how="new")
        (self.sdir / "log" / "2026-01-01.md").write_text("\n".join(f"- ev{i}" for i in range(50)))
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

    def test_session_start_emits_context_and_marks_read(self):
        inbox.append(self.shipdir, "impl", "pm", "hello")
        code, out, _ = self.run_hook(hooks.session_start, {"source": "compact", "session_id": "x"})
        self.assertEqual(code, 0)
        data = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(data["hookEventName"], "SessionStart")
        self.assertIn("(compact)", data["additionalContext"])
        self.assertIn("hello", data["additionalContext"])
        self.assertEqual(inbox.unread(self.shipdir, "impl"), [])

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
        pidfile.write_text("1\n")  # launchd: always alive
        self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 0)

    def test_wait_deadline_exits_when_not_up(self):
        self.assertEqual(self.run_hook(hooks.wait_deadline, {})[0], 0)

    def test_deny_dialog(self):
        code, out, _ = self.run_hook(hooks.deny_dialog, {"tool_name": "Write", "tool_input": {"file_path": "x"}})
        d = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(d["decision"]["behavior"], "deny")
        log = next((self.shipdir / "seats/impl/log").glob("*.md")).read_text()
        self.assertIn("自動で拒否: Write", log)


if __name__ == "__main__":
    unittest.main()
