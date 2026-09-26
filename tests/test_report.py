"""report.py: the captain's daily report (design-p1 §2).

No notification leaves the test: the template's ``notify.via`` is empty, and
where a channel is set, ``notify.notify`` is replaced.
"""
import io
import json
import time
from contextlib import redirect_stdout
from datetime import datetime
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import board as board_mod
from yamato import deadline, events, inject, notify, pr, report, roster
from yamato.util import YamatoError

DATE = "2026-09-26"
T0 = datetime(2026, 9, 26, 9, 0).timestamp()   # 09:00 local


def at(h, m=0):
    return datetime(2026, 9, 26, h, m).timestamp()


class ReportTestCase(ShipTestCase):
    def setUp(self):
        super().setUp()
        agents = mock.patch("yamato.claude.agents", return_value=[])
        self.agents = agents.start()
        self.addCleanup(agents.stop)

    def brd(self):
        return board_mod.Board(self.shipdir, self.team())

    def decision(self, did, title, decider, state="open", body="", **extra):
        meta = {"id": did, "title": title, "kind": "decision", "decider": decider, "state": state, **extra}
        sub = "archive" if state == "done" else "items"
        path = self.shipdir / "board" / sub / f"{did}.md"
        path.write_text(board_mod.dumps(meta, body), encoding="utf-8")

    def usage(self, ts, **tokens):
        with open(self.shipdir / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": ts, "seat": "impl", **tokens}) + "\n")

    def build(self, **kw):
        return report.build(self.shipdir, self.team(), DATE, now=at(12, 5), **kw)


class BuildTest(ReportTestCase):
    def test_sections_are_filled_from_board_events_and_usage(self):
        b = self.brd()
        t1 = b.add("ヘッダーの修正", {"assignee": "impl", "pr": "#11"})["id"]
        with mock.patch("yamato.events.time.time", return_value=at(10)):
            b.set(t1, {"state": "done"}, by="pm")
        t2 = b.add("ログイン", {"assignee": "impl", "state": "active"})["id"]
        self.decision("D-007", "認証方式", "owner",
                      body="## 背景\nx\n## 選択肢と推し\n- A: JWT (推し。モバイルから使う)\n- B: セッション\n")
        self.decision("D-008", "設計の小さな判断", "pm")            # an AI decider: not the owner's
        self.decision("D-006", "閉じた判断", "owner", state="done")
        t3 = b.add("決済", {"assignee": "impl"})["id"]
        b.set(t3, {"state": "blocked", "blocked_on": t2})
        b.add("未着手", {})
        events.emit(self.shipdir, events.DECISION_OPEN, item="D-007", now=at(9))
        events.emit(self.shipdir, events.SHIFT_START, seat="pm", now=at(9, 2), data={"shiftNo": 1})
        events.emit(self.shipdir, events.FORCE_STOP, seat="impl", now=at(12, 5),
                    data={"reason": "down-force", "shiftNo": 2})
        events.emit(self.shipdir, events.SHIFT_END, seat="impl", now=at(12, 5),
                    data={"reason": "down-force", "shiftNo": 2, "handoffWritten": False})
        events.emit(self.shipdir, events.SHIFT_END, seat="pm", now=at(11),
                    data={"reason": "exited", "shiftNo": 1, "handoffWritten": False})
        for tool in ("Bash", "Bash", "Write"):
            events.emit(self.shipdir, events.PERMISSION_DENIED, seat="impl", now=at(10), data={"tool": tool})
        events.emit(self.shipdir, notify.NOTIFY_FAILED, now=at(10), summary="通知 slack に失敗: 環境変数が空")
        events.emit(self.shipdir, events.PERMISSION_DENIED, seat="impl", now=at(10) - 86400, data={"tool": "Old"})
        self.usage(at(10), input_tokens=1_200_000, output_tokens=85_000)
        self.usage(at(11), input_tokens=1000, output_tokens=500)
        self.usage(at(10) - 86400, input_tokens=9_999_999)   # yesterday

        text = self.build()
        secs = report.sections(text)
        self.assertTrue(text.startswith(f"# t1 日報 {DATE} (稼働 09:02–12:05)"), text.splitlines()[0])
        self.assertEqual(secs["一言"], report.CAPTAIN_BLANK)
        self.assertEqual(secs["明日"], report.CAPTAIN_BLANK)
        self.assertIn("## owner の判断待ち (1 件)", text)
        self.assertIn("- D-007 認証方式 (待ち 3 時間) … 推し: A: JWT (推し。モバイルから使う)",
                      secs["owner の判断待ち"])
        self.assertNotIn("D-008", text)
        self.assertNotIn("D-006", text)
        self.assertEqual(secs["今日終わったもの"], f"- {t1} ヘッダーの修正 (impl, PR #11)")
        moving = secs["動いているもの・止まっているもの"]
        self.assertIn(f"- {t2} impl 実装中: ログイン", moving)
        self.assertIn(f"- {t3} blocked: {t2} (impl: 決済)", moving)
        self.assertIn("- 未着手 1 件", moving)
        anomalies = secs["異常"]
        self.assertIn("impl が 12:05 に強制停止 (down-force) (引き継ぎなし)", anomalies)
        self.assertIn("pm が 11:00 に引き継ぎなしで終了 (exited)", anomalies)
        self.assertEqual(anomalies.count("強制停止"), 1)   # the force_stop's shift_end is not listed twice
        self.assertIn("- 権限の拒否 3 件 (impl: Bash ×2, Write ×1)", anomalies)
        self.assertIn("通知 slack に失敗", anomalies)
        self.assertNotIn("Old", anomalies)
        self.assertEqual(secs["使用量"], "- シフト 2 回 / 合計 入力 1.2M・出力 86k・cache 0 トークン (席別は usage.jsonl)")
        self.assertLessEqual(len(text.splitlines()), report.MAX_LINES)

    def test_span_runs_to_now_while_a_seat_is_still_on_shift(self):
        # e2e-p1: the captain writes the report after the members stopped, before its own seat-stop
        events.emit(self.shipdir, events.SHIFT_START, seat="pm", now=at(9, 2), data={"shiftNo": 1})
        events.emit(self.shipdir, events.SHIFT_START, seat="impl", now=at(9, 3), data={"shiftNo": 1})
        events.emit(self.shipdir, events.SHIFT_END, seat="impl", now=at(9, 30), data={"shiftNo": 1})
        self.assertTrue(self.build().startswith(f"# t1 日報 {DATE} (稼働 09:02–12:05)"))
        events.emit(self.shipdir, events.SHIFT_END, seat="pm", now=at(10), data={"shiftNo": 1})
        self.assertTrue(self.build().startswith(f"# t1 日報 {DATE} (稼働 09:02–10:00)"))

    def test_empty_ship(self):
        secs = report.sections(self.build())
        for name in ("owner の判断待ち", "今日終わったもの", "動いているもの・止まっているもの", "異常"):
            self.assertEqual(secs[name], "- なし")
        self.assertEqual(secs["使用量"], "- シフト 0 回")

    def test_facts_only_says_why_the_captain_did_not_write(self):
        secs = report.sections(self.build(facts_only="強制停止 (down-force)"))
        self.assertEqual(secs["一言"], "captain が書けなかった (理由: 強制停止 (down-force))")
        self.assertEqual(secs["明日"], "captain が書けなかった (理由: 強制停止 (down-force))")

    def test_capped_at_60_lines(self):
        b = self.brd()
        for i in range(30):
            b.add(f"動く {i}", {"state": "active", "assignee": "impl"})
            b.add(f"止まる {i}", {"state": "blocked"})
            self.decision(f"D-{i:03d}", f"判断 {i}", "owner")
            events.emit(self.shipdir, events.PERMISSION_DENIED, seat=f"s{i}", now=at(10), data={"tool": "Bash"})
            events.emit(self.shipdir, events.FORCE_STOP, seat=f"s{i}", now=at(10), data={"reason": "x"})
        text = self.build()
        self.assertLessEqual(len(text.splitlines()), report.MAX_LINES)
        self.assertIn("## 明日", text)
        self.assertIn("…ほか", text)

    def test_seat_stuck_on_a_permission_prompt(self):
        roster.start_shift(self.shipdir, "impl", session_id="sid-impl", short_id="sid", session_name="t1.impl",
                           how="new")
        self.agents.return_value = [{"sessionId": "sid-impl", "pid": 1, "waitingFor": "permission prompt"}]
        self.assertIn("impl が権限の確認で止まっている", report.sections(self.build())["異常"])
        self.assertNotIn("権限の確認", report.sections(self.build(live=False))["異常"])

    def test_listing_failure_does_not_fail_the_report(self):
        self.agents.side_effect = YamatoError("claude がない")
        self.assertEqual(report.sections(self.build())["異常"], "- なし")

    def test_merges_are_done_work_even_while_the_item_is_open(self):
        b = self.brd()
        merged = b.add("ログイン", {"assignee": "impl", "pr": "12", "state": "active"})["id"]
        both = b.add("決済", {"assignee": "impl", "pr": "13"})["id"]
        old = b.add("昨日の merge", {"assignee": "impl", "pr": "14"})["id"]
        pm = {"strategy": "squash", "mergedBy": "pm"}
        events.emit(self.shipdir, pr.PR_MERGE, seat="impl", item=merged, by="pm", now=at(10),
                    data={"pr": "12", **pm})
        events.emit(self.shipdir, pr.PR_MERGE, seat="impl", item=merged, by="owner", now=at(11),
                    data={"pr": "12", "strategy": "squash", "mergedBy": "owner", "alreadyMerged": True})
        events.emit(self.shipdir, pr.PR_MERGE, seat="impl", item=both, by="pm", now=at(10, 30), data={"pr": "13", **pm})
        with mock.patch("yamato.events.time.time", return_value=at(11)):
            b.set(both, {"state": "done"}, by="pm")
        events.emit(self.shipdir, pr.PR_MERGE, seat="impl", item=old, by="pm", now=at(10) - 86400,
                    data={"pr": "14", **pm})
        events.emit(self.shipdir, pr.PR_MERGE_FAILED, seat="impl", item=old, by="pm", now=at(10),
                    data={"reason": "条件を満たさない", "unmet": ["review: x"]})   # refused: not work done
        done = report.sections(self.build())["今日終わったもの"].splitlines()
        self.assertEqual(done, [f"- {merged} ログイン (impl, PR 12 を pm が merge)",
                                f"- {both} 決済 (impl, PR 13 を pm が merge)"])

    def test_merge_conflicts_and_decisions_closed_by_a_non_decider_are_anomalies(self):
        events.emit(self.shipdir, pr.PR_CONFLICT, seat="impl", item="T-002", by="pm", now=at(10, 30),
                    data={"pr": "2", "mergedItem": "T-001", "mergedPr": "1", "mergedBy": "pm",
                          "column": "rebase", "notified": "impl"})
        events.emit(self.shipdir, pr.PR_CONFLICT, seat="pm", item="T-003", by="pm", now=at(10, 40),
                    data={"pr": "3", "mergedItem": "T-001", "mergedPr": "1", "mergedBy": "pm"})
        events.emit(self.shipdir, events.DECISION_CLOSE, seat="owner", item="D-001", by="pm", now=at(11),
                    data={"decider": "owner", "closed_by": "pm", "on_behalf_of": "owner", "by_decider": False})
        events.emit(self.shipdir, events.DECISION_CLOSE, seat="pm", item="D-002", by="pm", now=at(11, 5),
                    data={"decider": "pm", "closed_by": "pm", "by_decider": True})
        events.emit(self.shipdir, pr.PR_CONFLICT, seat="impl", item="T-009", by="pm", now=at(10) - 86400,
                    data={"pr": "9", "mergedItem": "T-001", "mergedPr": "1"})
        events.emit(self.shipdir, pr.PR_MERGE_FAILED, seat="impl", item="T-004", by="pm", now=at(10),
                    data={"reason": "x"})   # a refused merge is the safety net working, not an anomaly
        anomalies = report.sections(self.build())["異常"].splitlines()
        self.assertEqual(anomalies, [
            "- T-002 の PR #2 が 10:30 に衝突 (T-001 の PR #1 を merge)。impl に rebase を頼んだ",
            "- T-003 の PR #3 が 10:40 に衝突 (T-001 の PR #1 を merge)",
            "- D-001 の判断を decider (owner) 以外の pm が 11:00 に閉じた。--by は owner",
        ])
        self.assertEqual(report.summary(self.build())[1], "error")


class MakeAndSendTest(ReportTestCase):
    def test_make_does_not_overwrite_the_captains_text(self):
        path = report.make(self.shipdir, self.team(), DATE)
        self.assertEqual(path, self.shipdir / "reports" / "daily" / f"{DATE}.md")
        path.write_text(path.read_text().replace(report.CAPTAIN_BLANK, "書いた", 1))
        with self.assertRaises(YamatoError):
            report.make(self.shipdir, self.team(), DATE)
        self.assertIn("書いた", path.read_text())
        report.make(self.shipdir, self.team(), DATE, force=True)
        self.assertNotIn("書いた", path.read_text())
        made = events.read(self.shipdir, kinds=report.REPORT_MADE)
        self.assertEqual([e["data"]["date"] for e in made], [DATE, DATE])

    def test_bad_date(self):
        with self.assertRaises(YamatoError):
            report.make(self.shipdir, self.team(), "26-09-2026")

    def test_summary_is_word_decisions_anomalies_within_20_lines(self):
        text = ("# t1 日報\n## 一言\n今日の要点\n\n## owner の判断待ち (1 件)\n- D-1 x\n\n## 今日終わったもの\n- T-1\n\n"
                "## 異常\n" + "".join(f"- a{i}\n" for i in range(30)) + "\n## 明日\n- あした\n")
        body, level = report.summary(text)
        lines = body.splitlines()
        self.assertLessEqual(len(lines), report.NOTIFY_LINES)
        self.assertEqual(lines[:2], ["## 一言", "今日の要点"])
        self.assertIn("## owner の判断待ち (1 件)", lines)
        self.assertNotIn("T-1", body)
        self.assertNotIn("あした", body)
        self.assertEqual(level, "error")
        self.assertEqual(report.summary(text.split("## 異常")[0] + "## 異常\n- なし\n")[1], "waiting")

    def test_send_notifies_with_the_file_contents(self):
        path = report.make(self.shipdir, self.team(), DATE)
        path.write_text(path.read_text().replace(report.CAPTAIN_BLANK, "T-042 は merge 待ち", 1))
        with mock.patch("yamato.report.notify.notify", return_value=["通知 slack: 送った"]) as n:
            self.assertEqual(report.send(self.shipdir, self.team(), DATE), ["通知 slack: 送った"])
        [call] = n.call_args_list
        _, title, body, level = call.args
        self.assertEqual(title, f"yamato t1: 日報 {DATE}")
        self.assertIn("T-042 は merge 待ち", body)
        self.assertIn(str(path), body)
        self.assertEqual(level, "info")
        self.assertTrue(report.was_sent(self.shipdir, DATE))

    def test_send_without_a_report(self):
        with self.assertRaises(YamatoError):
            report.send(self.shipdir, self.team(), DATE)


class SafetyNetTest(ReportTestCase):
    def test_makes_facts_only_and_sends_once(self):
        with mock.patch("yamato.report.notify.notify", return_value=[]) as n:
            lines = report.safety_net(self.shipdir, self.team(), "強制停止 (down-force)", DATE)
            self.assertIn("事実だけで作った", lines[0])
            self.assertEqual(report.safety_net(self.shipdir, self.team(), "again", DATE), [])
        self.assertEqual(n.call_count, 1)
        self.assertIn("captain が書けなかった (理由: 強制停止 (down-force))",
                      report.report_path(self.shipdir, DATE).read_text())

    def test_sends_the_captains_unsent_report_as_is(self):
        path = report.make(self.shipdir, self.team(), DATE)
        path.write_text(path.read_text().replace(report.CAPTAIN_BLANK, "captain の一言", 1))
        with mock.patch("yamato.report.notify.notify", return_value=[]) as n:
            self.assertEqual(report.safety_net(self.shipdir, self.team(), "x", DATE), [])
        self.assertIn("captain の一言", n.call_args.args[2])
        self.assertIn("captain の一言", path.read_text())

    def test_nothing_after_the_captain_sent(self):
        report.make(self.shipdir, self.team(), DATE)
        report.send(self.shipdir, self.team(), DATE)
        with mock.patch("yamato.report.notify.notify") as n:
            report.safety_net(self.shipdir, self.team(), "x", DATE)
        n.assert_not_called()

    def test_second_stop_of_the_day_refreshes_facts_and_sends_an_update(self):
        # e2e-p1 E: the captain reported at its first stop; work went on afterwards
        day = time.strftime("%Y-%m-%d")
        path = report.make(self.shipdir, self.team(), day)
        path.write_text(path.read_text().replace(report.CAPTAIN_BLANK, "一言 A", 1)
                        .replace(report.CAPTAIN_BLANK, "明日 B\n- 二行目", 1))
        with mock.patch("yamato.report.notify.notify", return_value=[]):
            report.send(self.shipdir, self.team(), day)
        events.emit(self.shipdir, events.SHIFT_END, seat="pm", data={"shiftNo": 1, "handoffWritten": True})
        self.assertFalse(report.needs_send(self.shipdir, day))   # the captain's own seat-stop is no news
        self.decision("D-009", "merge する?", "owner")
        events.emit(self.shipdir, events.DECISION_OPEN, item="D-009")
        self.assertTrue(report.needs_send(self.shipdir, day))
        with mock.patch("yamato.report.notify.notify", return_value=[]) as n:
            lines = report.safety_net(self.shipdir, self.team(), "down", day)
            self.assertEqual(report.safety_net(self.shipdir, self.team(), "again", day), [])
        self.assertIn("事実の節を作り直した", lines[0])
        [call] = n.call_args_list
        self.assertEqual(call.args[1], f"yamato t1: 日報 {day} (更新)")
        text = path.read_text()
        self.assertIn("D-009 merge する?", text)
        secs = report.sections(text)
        self.assertEqual((secs[report.S_WORD], secs[report.S_TOMORROW]), ("一言 A", "明日 B\n- 二行目"))
        self.assertTrue(events.read(self.shipdir, kinds=report.REPORT_SENT)[-1]["data"]["update"])

    def test_cli_daily_on_an_existing_report_keeps_the_captains_text(self):
        from yamato import cli

        day = time.strftime("%Y-%m-%d")
        path = report.make(self.shipdir, self.team(), day)
        path.write_text(path.read_text().replace(report.CAPTAIN_BLANK, "一言 A", 1))
        report.send(self.shipdir, self.team(), day)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["report", "daily", str(self.shipdir)]), 0)
        self.assertIn("要らない", out.getvalue())
        events.emit(self.shipdir, events.BOARD_ADD, item="T-009")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["report", "daily", str(self.shipdir)]), 0)
        self.assertIn("「更新」", out.getvalue())
        self.assertIn("一言 A", path.read_text())
        with mock.patch("yamato.report.notify.notify", return_value=[]) as n:
            report.send(self.shipdir, self.team(), day)
        self.assertTrue(n.call_args.args[1].endswith("(更新)"))

    def test_off(self):
        team = self.team()
        team["report"] = {"daily": "off"}
        self.assertEqual(report.safety_net(self.shipdir, team, "x", DATE), [])
        self.assertFalse(report.report_path(self.shipdir, DATE).exists())

    def test_never_raises(self):
        with mock.patch("yamato.report.make", side_effect=OSError("disk full")):
            self.assertEqual(report.safety_net(self.shipdir, self.team(), "x", DATE), [])

    def test_down_without_live_seats_makes_the_report(self):
        from yamato import seat

        seat.prepare(self.shipdir)
        deadline.write(self.shipdir, limit=3600, grace=60, token="tok")
        with mock.patch("yamato.seat.spawn_watchdog"), redirect_stdout(io.StringIO()) as out:
            seat.down(self.shipdir, False)
        today = time.strftime("%Y-%m-%d")
        self.assertTrue(report.report_path(self.shipdir, today).exists())
        self.assertIn("事実だけで作った", out.getvalue())
        self.assertIn("captain が動いていなかった", report.report_path(self.shipdir, today).read_text())

    def test_down_of_a_ship_not_up_makes_nothing(self):
        from yamato import seat

        seat.prepare(self.shipdir)
        with redirect_stdout(io.StringIO()):
            seat.down(self.shipdir, False)
        self.assertFalse(report.reports_dir(self.shipdir).exists())


class InjectTest(ReportTestCase):
    def test_captain_reads_only_three_sections_of_the_latest_report(self):
        d = report.reports_dir(self.shipdir)
        d.mkdir(parents=True)
        (d / "2026-09-25.md").write_text("# old\n## 一言\n古い\n")
        (d / f"{DATE}.md").write_text(
            "# t1 日報\n## 一言\n新しい一言\n\n## owner の判断待ち (1 件)\n- D-007 認証\n\n"
            "## 今日終わったもの\n- T-041 ヘッダー\n\n## 異常\n- なし\n\n## 明日\n- T-044 から\n")
        text, _ = inject.build(self.shipdir, self.team(), "pm")
        part = text.split("## 前回の日報")[1]
        self.assertIn(f"({DATE}。全文: {d / (DATE + '.md')})", part)
        for want in ("### 一言", "新しい一言", "### owner の判断待ち (1 件)", "- D-007 認証", "### 明日", "- T-044 から"):
            self.assertIn(want, part)
        for not_want in ("古い", "T-041", "異常"):
            self.assertNotIn(not_want, part)

    def test_only_roles_that_ask_for_it(self):
        report.make(self.shipdir, self.team(), DATE)
        self.assertNotIn("前回の日報", inject.build(self.shipdir, self.team(), "impl")[0])

    def test_no_report_yet(self):
        self.assertIn("## 前回の日報\n(なし)", inject.build(self.shipdir, self.team(), "pm")[0])


class ConfigAndCliTest(ReportTestCase):
    def test_template_default_is_on_down(self):
        self.assertEqual(self.team()["report"], {"daily": "on_down"})
        self.assertEqual(report.daily_mode({}), "on_down")

    def test_off_and_bad_values(self):
        from yamato.team import load_team

        ty = self.shipdir / "team.yaml"
        base = ty.read_text()
        ty.write_text(base.replace("daily: on_down", "daily: off"))
        self.assertEqual(load_team(self.shipdir)["report"], {"daily": "off"})
        ty.write_text(base.replace("daily: on_down", "daily: always"))
        with self.assertRaises(YamatoError):
            load_team(self.shipdir)

    def test_cli_daily_and_send(self):
        from yamato import cli

        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["report", "daily", str(self.shipdir), "--date", DATE]), 0)
        self.assertIn("日報の下書きを作った", out.getvalue())
        self.assertIn("report send", out.getvalue())
        self.assertFalse(report.was_sent(self.shipdir, DATE))
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["report", "send", str(self.shipdir), "--date", DATE]), 0)
        self.assertIn("notify.via が空", out.getvalue())
        self.assertTrue(report.was_sent(self.shipdir, DATE))

    def test_cli_facts_only_sends_at_once(self):
        from yamato import cli

        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["report", "daily", str(self.shipdir), "--date", DATE, "--facts-only",
                                       "--reason", "手で"]), 0)
        self.assertTrue(report.was_sent(self.shipdir, DATE))
        self.assertIn("captain が書けなかった (理由: 手で)", report.report_path(self.shipdir, DATE).read_text())
