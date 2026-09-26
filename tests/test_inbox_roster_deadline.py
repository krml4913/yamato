import json
import tempfile
import unittest
from pathlib import Path

from yamato import deadline, inbox, roster


class InboxTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ship = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_unread_and_cursor(self):
        for i in range(3):
            e = inbox.append(self.ship, "impl", "pm", f"m{i}")
        self.assertEqual(e["n"], 3)
        self.assertEqual([x["text"] for x in inbox.unread(self.ship, "impl")], ["m0", "m1", "m2"])
        inbox.mark_read(self.ship, "impl", 2)
        self.assertEqual([x["n"] for x in inbox.unread(self.ship, "impl")], [3])
        # the cursor lives in its own file; the inbox itself is append-only
        self.assertEqual((self.ship / "seats/impl/inbox.cursor").read_text().strip(), "2")
        self.assertEqual(len((self.ship / "seats/impl/inbox.jsonl").read_text().splitlines()), 3)
        inbox.mark_read(self.ship, "impl", 1)  # never moves backwards
        self.assertEqual(inbox.cursor(self.ship, "impl"), 2)

    def test_torn_line_is_skipped(self):
        inbox.append(self.ship, "impl", "pm", "ok")
        with open(self.ship / "seats/impl/inbox.jsonl", "a") as f:
            f.write('{"n": 2, "te\n')
        self.assertEqual([x["text"] for x in inbox.unread(self.ship, "impl")], ["ok"])
        # numbering continues from the last valid entry, never reusing a number
        self.assertEqual(inbox.append(self.ship, "impl", "pm", "next")["n"], 2)
        with open(self.ship / "seats/impl/inbox.jsonl", "a") as f:
            f.write("garbage\n")
        self.assertEqual(inbox.append(self.ship, "impl", "pm", "after")["n"], 3)

    def test_format_truncates(self):
        e = {"n": 1, "ts": 0, "from": "pm", "text": "x" * 50}
        self.assertIn("40 文字省略", inbox.format_entry(e, 10))


class RosterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ship = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def start(self, sid="11111111-aaaa"):
        return roster.start_shift(self.ship, "impl", session_id=sid, short_id=sid[:8],
                                  session_name="t1.impl", how="new", now=100)

    def test_shift_numbers_and_full_session_id(self):
        r = self.start()
        self.assertEqual((r["shiftNo"], r["state"], r["sessionId"]), (1, roster.ON_SHIFT, "11111111-aaaa"))
        roster.end_shift(self.ship, "impl", reason="seat-stop", handoff_written=True, now=200)
        r = self.start("22222222-bbbb")
        self.assertEqual(r["shiftNo"], 2)
        self.assertFalse(r["prevEndedWithoutHandoff"])
        data = json.loads((self.ship / "roster.json").read_text())
        self.assertEqual([s["endReason"] for s in data["shifts"]], ["seat-stop", None])

    def test_no_handoff_is_recorded_and_carried(self):
        self.start()
        roster.end_shift(self.ship, "impl", reason="forced", handoff_written=False,
                         note=roster.NO_HANDOFF_NOTE)
        rec = roster.seat(self.ship, "impl")
        self.assertEqual((rec["state"], rec["note"]), (roster.OFF, "引き継ぎなしで終了"))
        self.assertTrue(self.start()["prevEndedWithoutHandoff"])

    def test_unclean_previous_shift_counts_as_no_handoff(self):
        self.start()
        self.assertTrue(self.start()["prevEndedWithoutHandoff"])

    def test_stopping(self):
        self.start()
        self.assertEqual(roster.mark_stopping(self.ship, "impl", handoff_written=True)["state"], roster.STOPPING)


class DeadlineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ship = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_phases(self):
        self.assertEqual(deadline.phase(deadline.read(self.ship)), deadline.NOT_UP)
        d = deadline.write(self.ship, limit=600, grace=60, token="t", now=1000)
        self.assertEqual((d["deadline"], d["graceUntil"]), (1600, 1660))
        self.assertEqual(deadline.phase(d, 1599), deadline.RUNNING)
        self.assertEqual(deadline.phase(d, 1600), deadline.OVER)
        self.assertEqual(deadline.phase(d, 1659), deadline.OVER)
        self.assertEqual(deadline.phase(d, 1660), deadline.FORCE)
        self.assertIn("残り", deadline.describe(d, 1000))
        self.assertIn("終業中", deadline.describe(d, 1610))

    def test_end_now_starts_grace(self):
        deadline.write(self.ship, limit=3600, grace=120, token="t", now=1000)
        d = deadline.end_now(self.ship, now=1500)
        self.assertEqual((d["deadline"], d["graceUntil"]), (1500, 1620))
        self.assertEqual(deadline.phase(deadline.read(self.ship), 1501), deadline.OVER)
        d = deadline.end_now(self.ship, now=1550)  # already over: unchanged
        self.assertEqual(d["deadline"], 1500)

    def test_end_now_without_up(self):
        self.assertIsNone(deadline.end_now(self.ship))


if __name__ == "__main__":
    unittest.main()
