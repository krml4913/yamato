"""feed.py: events.jsonl の流し見 (T-007, owner 依頼 inbox #14)。

sleep は使わない。follow のポーリング間隔は ``feed.POLL`` を
``mock.patch.object`` で 0 にして、別スレッドから追記しつつ ``next()`` を
呼んで確かめる。
"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from yamato import events, feed


def _write(shipdir, kind, **kw):
    return events.emit(shipdir, kind, **kw)


class FeedTailTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.shipdir = Path(self._tmp.name)

    def test_no_file_yet_and_no_follow_yields_nothing(self):
        self.assertEqual(list(feed.tail(self.shipdir, follow=False)), [])

    def test_yields_only_the_last_n_lines_in_file_order(self):
        for i in range(20):
            _write(self.shipdir, events.SEND, seat="pm", summary=f"msg {i}", now=1000.0 + i)
        out = list(feed.tail(self.shipdir, lines=5, follow=False))
        self.assertEqual([e["summary"] for e in out], [f"msg {i}" for i in range(15, 20)])

    def test_lines_zero_skips_history(self):
        _write(self.shipdir, events.SEND, seat="pm", summary="old")
        out = list(feed.tail(self.shipdir, lines=0, follow=False))
        self.assertEqual(out, [])

    def test_kind_filter_applies_to_history(self):
        _write(self.shipdir, events.SEND, seat="pm", summary="a send")
        _write(self.shipdir, events.SHIFT_START, seat="impl", summary="a start")
        _write(self.shipdir, events.SEND, seat="pm", summary="another send")
        out = list(feed.tail(self.shipdir, kinds=events.SEND, follow=False))
        self.assertEqual([e["summary"] for e in out], ["a send", "another send"])

    def test_kind_filter_accepts_an_iterable(self):
        _write(self.shipdir, events.SEND, seat="pm", summary="a send")
        _write(self.shipdir, events.SHIFT_START, seat="impl", summary="a start")
        _write(self.shipdir, events.BOARD_SET, seat="impl", summary="a set")
        out = list(feed.tail(self.shipdir, kinds=[events.SEND, events.SHIFT_START], follow=False))
        self.assertEqual({e["kind"] for e in out}, {events.SEND, events.SHIFT_START})

    def test_torn_lines_are_skipped(self):
        _write(self.shipdir, events.SEND, seat="pm", summary="ok")
        with open(self.shipdir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write('{"torn\n')
        _write(self.shipdir, events.BOARD_SET, seat="impl", summary="ok2")
        out = list(feed.tail(self.shipdir, follow=False))
        self.assertEqual([e["summary"] for e in out], ["ok", "ok2"])

    def test_follow_yields_newly_appended_lines_without_sleeping(self):
        _write(self.shipdir, events.SEND, seat="pm", summary="history")
        with mock.patch.object(feed, "POLL", 0):
            it = feed.tail(self.shipdir, lines=1, follow=True)
            first = next(it)
            self.assertEqual(first["summary"], "history")

            got = []

            def reader():
                got.append(next(it))

            t = threading.Thread(target=reader)
            t.start()
            time.sleep(0.05)  # give the reader thread a moment to start blocking on readline
            _write(self.shipdir, events.SHIFT_END, seat="impl", summary="new one")
            t.join(timeout=5)
            self.assertFalse(t.is_alive())
            self.assertEqual(got[0]["summary"], "new one")
            it.close()

    def test_follow_waits_for_the_file_to_appear(self):
        with mock.patch.object(feed, "POLL", 0):
            it = feed.tail(self.shipdir, follow=True)
            got = []

            def reader():
                got.append(next(it))

            t = threading.Thread(target=reader)
            t.start()
            time.sleep(0.05)
            _write(self.shipdir, events.SEND, seat="pm", summary="first ever")
            t.join(timeout=5)
            self.assertFalse(t.is_alive())
            self.assertEqual(got[0]["summary"], "first ever")
            it.close()

    def test_kind_filter_applies_while_following(self):
        with mock.patch.object(feed, "POLL", 0):
            it = feed.tail(self.shipdir, kinds=events.SEND, follow=True)
            got = []

            def reader():
                got.append(next(it))

            t = threading.Thread(target=reader)
            t.start()
            time.sleep(0.05)
            _write(self.shipdir, events.SHIFT_START, seat="impl", summary="filtered out")
            _write(self.shipdir, events.SEND, seat="pm", summary="kept")
            t.join(timeout=5)
            self.assertFalse(t.is_alive())
            self.assertEqual(got[0]["summary"], "kept")
            it.close()


class FeedFormatTest(unittest.TestCase):
    def test_format_line_has_time_kind_who_item_summary(self):
        e = {"ts": 1_700_000_000.0, "kind": "send", "by": "pm", "seat": None, "item": "T-001",
             "summary": "hello"}
        line = feed.format_line(e, color=False)
        self.assertIn("send", line)
        self.assertIn("pm", line)
        self.assertIn("T-001", line)
        self.assertIn("hello", line)
        self.assertNotIn("\033[", line)

    def test_format_line_prefers_by_over_seat(self):
        e = {"kind": "board_set", "by": "impl-2", "seat": "pm", "item": "T-001", "summary": "x"}
        self.assertIn("impl-2", feed.format_line(e, color=False))

    def test_format_line_falls_back_to_seat_when_by_is_missing(self):
        e = {"kind": "shift_start", "by": None, "seat": "impl", "item": None, "summary": "x"}
        self.assertIn("impl", feed.format_line(e, color=False))

    def test_color_wraps_the_kind_field_in_its_sgr_code(self):
        e = {"kind": "force_stop", "by": "pm", "item": None, "summary": "x"}
        line = feed.format_line(e, color=True)
        self.assertIn(f"\033[{feed.color_for('force_stop')}m", line)
        self.assertIn(feed.RESET, line)

    def test_abnormal_kinds_are_red(self):
        for kind in ("force_stop", "shift_failed", "launch_failed", "permission_denied",
                     "spin_suspected", "duplicate_suspected", "captain_gap", "notify_failed",
                     "pr_open_failed", "pr_merge_failed", "pr_conflict", "worktree_add_failed",
                     "worktree_rm_failed"):
            self.assertEqual(feed.color_for(kind), 31, kind)

    def test_send_shift_decision_pr_report_have_distinct_non_default_colors(self):
        for kind in ("send", "shift_start", "shift_end", "decision_open", "decision_close",
                     "pr_open", "pr_merge", "report_made", "report_sent"):
            self.assertNotEqual(feed.color_for(kind), feed._DEFAULT_COLOR, kind)

    def test_unknown_kind_gets_the_default_color(self):
        self.assertEqual(feed.color_for("board_set"), feed._DEFAULT_COLOR)


class ColorEnabledTest(unittest.TestCase):
    def test_no_color_env_disables_color_even_on_a_tty(self):
        stream = mock.Mock()
        stream.isatty.return_value = True
        with mock.patch.dict("os.environ", {"NO_COLOR": "1"}):
            self.assertFalse(feed._color_enabled(stream))

    def test_non_tty_disables_color(self):
        stream = mock.Mock()
        stream.isatty.return_value = False
        with mock.patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("NO_COLOR", None)
            self.assertFalse(feed._color_enabled(stream))

    def test_tty_without_no_color_enables_color(self):
        stream = mock.Mock()
        stream.isatty.return_value = True
        with mock.patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("NO_COLOR", None)
            self.assertTrue(feed._color_enabled(stream))

    def test_stream_without_isatty_is_treated_as_no_color(self):
        stream = object()  # no isatty attribute at all
        with mock.patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("NO_COLOR", None)
            self.assertFalse(feed._color_enabled(stream))


class RunFeedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.shipdir = Path(self._tmp.name)

    def test_prints_formatted_lines_without_follow(self):
        _write(self.shipdir, events.SEND, seat="pm", by="pm", item="T-001", summary="hi")
        buf = mock.Mock()
        buf.isatty.return_value = False
        lines_written = []
        buf.write.side_effect = lambda s: lines_written.append(s)
        feed.run_feed(self.shipdir, follow=False, out=buf)
        text = "".join(lines_written)
        self.assertIn("send", text)
        self.assertIn("T-001", text)
        self.assertIn("hi", text)
        self.assertNotIn("\033[", text)  # non-tty out: no color


if __name__ == "__main__":
    unittest.main()
