"""Decisions (design-p1 §1): decide open / close / list, decisions/log.md, the team.yaml table."""
import io
import json
import os
import time
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import board, cli, decide, events, inbox, roster
from yamato.team import load_yaml, validate
from yamato.util import YamatoError

SID_IMPL = "i" * 36
SID_PM = "p" * 36


class DecideTestCase(ShipTestCase):
    def setUp(self):
        super().setUp()
        self._sid = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self.addCleanup(self._restore_sid)
        roster.start_shift(self.shipdir, "impl", session_id=SID_IMPL, short_id="iiiiiiii",
                           session_name="t1.impl", how="new")
        roster.start_shift(self.shipdir, "pm", session_id=SID_PM, short_id="pppppppp",
                           session_name="t1.pm", how="new")
        self.brd = board.Board(self.shipdir, self.team())

    def _restore_sid(self):
        if self._sid is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self._sid

    def as_seat(self, sid):
        return mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": sid})

    def quiet(self, fn, *args, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = fn(*args, **kw)
        return result, buf.getvalue()

    def open(self, sid=SID_IMPL, **kw):
        kw.setdefault("category", "design")
        kw.setdefault("title", "認証方式")
        team = self.team()
        with self.as_seat(sid):
            meta = decide.open_decision(self.shipdir, team, **kw)
            _, out = self.quiet(decide.deliver_open, self.shipdir, team, meta, out=print)
        return meta, out

    def close(self, dec_id, sid=SID_PM, team=None, **kw):
        kw.setdefault("choice", "JWT")
        kw.setdefault("reason", "モバイルからも使う")
        team = team or self.team()
        with self.as_seat(sid):
            result = decide.close_decision(self.shipdir, team, dec_id, **kw)
            _, out = self.quiet(decide.deliver_close, self.shipdir, team, result, out=print)
        return result, out

    def task(self, **fields):
        return self.brd.add("タスク", fields, by="pm")


class TeamTableTest(DecideTestCase):
    def data(self):
        return load_yaml(self.shipdir / "team.yaml")

    def test_template_defaults(self):
        team = self.team()
        self.assertEqual(team["decisions"]["merge"], {"decider": "owner", "when": "PR を main に入れる"})
        self.assertEqual(team["decisions"]["default"]["decider"], "pm")
        self.assertEqual(team["notify"]["decisions"], "digest")

    def test_short_form_and_long_form(self):
        d = self.data()
        d["decisions"] = {"merge": "owner", "design": {"decider": "impl", "when": "API"}}
        t = validate(d, self.shipdir)
        self.assertEqual(t["decisions"], {"merge": {"decider": "owner", "when": None},
                                          "design": {"decider": "impl", "when": "API"}})

    def test_bad_tables_are_refused(self):
        bad = [
            {"merge": "nobody"},                       # not a seat nor owner
            {"merge": {"when": "x"}},                  # no decider
            {"merge": {"decider": "pm", "extra": 1}},  # unknown key
            ["merge"],                                 # not a mapping
            {"Bad Cat": "pm"},
        ]
        for table in bad:
            with self.subTest(table=table), self.assertRaises(YamatoError):
                d = self.data()
                d["decisions"] = table
                validate(d, self.shipdir)

    def test_a_role_of_several_seats_is_not_a_decider(self):
        d = self.data()
        d["roles"]["impl"]["count"] = 2
        d["decisions"] = {"design": "impl"}
        with self.assertRaises(YamatoError):
            validate(d, self.shipdir)
        d["decisions"] = {"design": "impl-2"}
        self.assertEqual(validate(d, self.shipdir)["decisions"]["design"]["decider"], "impl-2")

    def test_notify_decisions(self):
        d = self.data()
        d["notify"]["decisions"] = "each"
        self.assertEqual(validate(d, self.shipdir)["notify"]["decisions"], "each")
        d["notify"]["decisions"] = "hourly"
        with self.assertRaises(YamatoError):
            validate(d, self.shipdir)

    def test_resolution(self):
        team = self.team()
        self.assertEqual(decide.resolve_decider(team, "merge"), "owner")
        self.assertEqual(decide.resolve_decider(team, "design"), "pm")
        self.assertEqual(decide.resolve_decider(team, "unknown"), "pm")   # default
        team["decisions"] = {}
        self.assertEqual(decide.resolve_decider(team, "merge"), "pm")     # no table: the hub


class CallerTest(DecideTestCase):
    def test_caller(self):
        self.assertEqual(decide.caller(self.shipdir), "owner")
        with self.as_seat(SID_IMPL):
            self.assertEqual(decide.caller(self.shipdir), "impl")
        with self.as_seat("x" * 36):
            self.assertEqual(decide.caller(self.shipdir), "owner")   # the human's own Claude Code session
        roster.start_shift(self.shipdir, "impl", session_id="n" * 36, short_id="nnnnnnnn",
                           session_name="t1.impl", how="new")
        with self.as_seat(SID_IMPL):   # an earlier shift of the seat
            self.assertEqual(decide.caller(self.shipdir), "impl")


class OpenTest(DecideTestCase):
    def test_open_creates_the_item_blocks_tasks_and_sends_to_the_decider(self):
        t1 = self.task(assignee="impl", state="active")
        t2 = self.task()
        meta, out = self.open(blocks=[t1["id"], t2["id"]], due="2026-10-01")
        self.assertEqual(meta["id"], "D-001")
        path = self.shipdir / "board/items/D-001.md"
        m, body = board.loads(path.read_text())
        self.assertEqual({k: m[k] for k in ("kind", "category", "decider", "opened_by", "state", "due", "links")},
                         {"kind": "decision", "category": "design", "decider": "pm", "opened_by": "impl",
                          "state": "open", "due": "2026-10-01", "links": ["T-001", "T-002"]})
        self.assertIn("## 決定\n(閉じるときに yamato が書く)", body)
        self.assertNotIn("blocks", m)   # one direction only (§1.1)

        a, _, _ = self.brd.read("T-001")
        b, _, _ = self.brd.read("T-002")
        self.assertEqual((a["state"], a["blocked_on"], a["pre_blocked_state"]), ("blocked", ["D-001"], "active"))
        self.assertEqual((b["state"], b["pre_blocked_state"]), ("blocked", "open"))

        [e] = events.read(self.shipdir, kinds=events.DECISION_OPEN)
        self.assertEqual((e["seat"], e["item"], e["by"]), ("pm", "D-001", "impl"))
        self.assertEqual(e["data"]["blocks"], ["T-001", "T-002"])

        [msg] = inbox.entries(self.shipdir, "pm")
        self.assertEqual(msg["from"], "impl")
        self.assertIn("D-001 の判断を頼む", msg["text"])
        self.assertIn(str(path), msg["text"])
        self.assertIn("inbox に記録した: pm", out)

    def test_ids_are_sequential_and_separate_from_tasks(self):
        self.task()
        self.assertEqual(self.open()[0]["id"], "D-001")
        self.assertEqual(self.open()[0]["id"], "D-002")
        self.assertEqual(self.task()["id"], "T-002")
        self.assertEqual([m["id"] for m in self.brd.items()], ["T-001", "T-002"])

    def test_body_file_is_kept(self):
        meta, _ = self.open(body="## 背景\nなぜ\n## 選択肢と推し\n- A (推し)\n")
        _, body, _ = self.brd.read(meta["id"])
        self.assertTrue(body.startswith("## 背景\nなぜ\n## 選択肢と推し\n- A (推し)"))
        self.assertIn("## 決定\n", body)

    def test_already_blocked_task_keeps_its_earlier_state(self):
        t = self.task(state="active")
        self.open(blocks=[t["id"]])
        self.open(blocks=[t["id"]])
        m, _, _ = self.brd.read(t["id"])
        self.assertEqual((m["blocked_on"], m["pre_blocked_state"]), (["D-001", "D-002"], "active"))

    def test_task_blocked_by_hand_stays_blocked_after_the_close(self):
        t = self.task(assignee="impl", state="active")
        self.brd.set(t["id"], {"state": "blocked"}, note="外部 API 待ち", by="impl")
        self.open(sid=SID_PM, blocks=[t["id"]])
        m, _, _ = self.brd.read(t["id"])
        self.assertEqual(m["pre_blocked_state"], "blocked")
        result, _ = self.close("D-001", sid=SID_PM)
        m, _, _ = self.brd.read(t["id"])
        self.assertEqual((m["state"], m["blocked_on"]), ("blocked", []))
        [msg] = [x for x in inbox.entries(self.shipdir, "impl") if "D-001 が決まった" in x["text"]]
        self.assertIn("blocked のまま", msg["text"])
        self.assertNotIn("止まりが解けた", msg["text"])

    def test_refusals_write_nothing(self):
        done = self.task(state="done")
        cases = [dict(blocks=["T-404"]), dict(blocks=[done["id"]]), dict(due="来週"), dict(title=" "),
                 dict(supersedes="D-404")]
        for kw in cases:
            with self.subTest(kw=kw), self.assertRaises(YamatoError):
                self.open(**kw)
        self.assertFalse(list((self.shipdir / "board/items").glob("D-*")))
        d, _ = self.open()
        with self.assertRaises(YamatoError):
            self.open(blocks=[d["id"]])   # a decision is not a task

    def test_owner_decider_digest_goes_to_the_inbox_without_a_notification(self):
        with mock.patch.object(decide.notify, "notify", return_value=[]) as n:
            meta, out = self.open(category="merge", title="t-001 を main に")
        self.assertEqual(meta["decider"], "owner")
        [msg] = inbox.entries(self.shipdir, "owner")
        self.assertIn("D-001", msg["text"])
        n.assert_not_called()
        self.assertIn("日報にまとめる", out)
        self.assertEqual(events.read(self.shipdir, kinds=events.SEND)[-1]["seat"], "owner")

    def test_owner_decider_urgent_or_each_notifies_now(self):
        with mock.patch.object(decide.notify, "notify", return_value=["通知 mac: exit 0"]) as n:
            _, out = self.open(category="merge", title="急ぎ", urgent=True)
        n.assert_called_once()
        self.assertIn("判断待ち D-001", n.call_args[0][1])
        self.assertEqual(n.call_args[0][3], "waiting")
        self.assertEqual(n.call_args.kwargs["shipdir"], self.shipdir)   # failures go to events (notify_failed)
        self.assertIn("通知 mac: exit 0", out)

        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("decisions: digest", "decisions: each"))
        with mock.patch.object(decide.notify, "notify", return_value=[]) as n:
            self.open(category="merge", title="1 件ずつ")
        n.assert_called_once()

    def test_decider_who_opened_it_gets_nothing(self):
        _, out = self.open(sid=SID_PM)
        self.assertEqual(inbox.entries(self.shipdir, "pm"), [])
        self.assertIn("開いた本人なので送らない", out)


class CloseTest(DecideTestCase):
    def test_close_records_everything_and_releases_the_tasks(self):
        t1 = self.task(assignee="impl", state="active")
        t2 = self.task()                        # no assignee: the hub hears
        self.open(blocks=[t1["id"], t2["id"]])
        result, out = self.close("D-001", sid=SID_PM)
        self.assertTrue(result["by_decider"])

        self.assertFalse((self.shipdir / "board/items/D-001.md").exists())
        m, body = board.loads((self.shipdir / "board/archive/D-001.md").read_text())
        self.assertEqual((m["state"], m["closed_by"], m["on_behalf_of"]), ("done", "pm", "pm"))
        self.assertIn("## 決定\n- 決定: JWT\n- 理由: モバイルからも使う\n- 決めた人: pm\n- 閉じた席: pm", body)
        self.assertNotIn("(閉じるときに yamato が書く)", body)
        self.assertIn("判断を閉じた: JWT", body.split("## 経緯")[1])

        a, _, _ = self.brd.read(t1["id"])
        b, _, _ = self.brd.read(t2["id"])
        self.assertEqual((a["state"], a["blocked_on"], a["pre_blocked_state"]), ("active", [], None))
        self.assertEqual((b["state"], b["blocked_on"]), ("open", []))

        [impl_msg] = inbox.entries(self.shipdir, "impl")
        self.assertIn("D-001 が決まった", impl_msg["text"])
        self.assertIn("T-001", impl_msg["text"])
        # T-002 has no assignee, so the hub hears -- but pm closed it itself
        self.assertEqual(inbox.entries(self.shipdir, "pm")[1:], [])   # [0] is the open request
        self.assertIn("閉じた本人なので送らない", out)

        log = (self.shipdir / "decisions/log.md").read_text()
        self.assertIn("## D-001 認証方式 (", log)
        self.assertIn(", pm)\n- 決定: JWT\n- 理由: モバイルからも使う\n- 止まっていたもの: T-001, T-002\n"
                      "- 項目: board/archive/D-001.md", log)

        [e] = events.read(self.shipdir, kinds=events.DECISION_CLOSE)
        self.assertEqual((e["seat"], e["item"], e["by"]), ("pm", "D-001", "pm"))
        self.assertEqual(e["data"]["unblocked"], ["T-001", "T-002"])
        self.assertTrue(e["data"]["by_decider"])

    def test_owner_in_their_own_claude_session_closes_as_the_decider(self):
        self.open(sid=SID_PM, category="merge")        # decider owner
        result, _ = self.close("D-001", sid="h" * 36)  # a session roster does not know
        self.assertTrue(result["by_decider"])
        self.assertEqual((result["meta"]["closed_by"], result["meta"]["on_behalf_of"]), ("owner", "owner"))

    def test_unassigned_task_goes_to_the_hub(self):
        t = self.task()
        self.open(blocks=[t["id"]], category="merge")     # decider owner
        self.close("D-001", sid=SID_IMPL, on_behalf_of="owner")
        msgs = [x for x in inbox.entries(self.shipdir, "pm") if "が決まった" in x["text"]]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["from"], "impl")

    def test_proxy_close_for_the_owner(self):
        self.open(category="merge", title="入れるか")
        result, _ = self.close("D-001", sid=SID_PM, on_behalf_of="owner", choice="入れる")
        self.assertTrue(result["by_decider"])
        m, body, _ = self.brd.read("D-001")
        self.assertEqual((m["closed_by"], m["on_behalf_of"]), ("pm", "owner"))
        self.assertIn("決めた人: owner (代筆: pm)", body)
        self.assertIn("## D-001 入れるか (", (self.shipdir / "decisions/log.md").read_text())
        self.assertIn(", owner / 代筆 pm)", (self.shipdir / "decisions/log.md").read_text())

    def test_a_non_decider_may_close_and_it_is_recorded(self):
        self.open(sid=SID_PM, category="merge")   # decider owner
        result, out = self.close("D-001", sid=SID_IMPL)
        self.assertFalse(result["by_decider"])
        m, body, _ = self.brd.read("D-001")
        self.assertEqual(m["state"], "done")
        self.assertIn("注意: decider (owner) 以外が閉じた", body)
        self.assertIn("注意: decider (owner) 以外が閉じた", (self.shipdir / "decisions/log.md").read_text())
        [e] = events.read(self.shipdir, kinds=events.DECISION_CLOSE)
        self.assertFalse(e["data"]["by_decider"])
        self.assertIn("以外が閉じた", e["summary"])

    def test_the_seat_that_opened_it_hears_when_the_owner_closes_it(self):
        # e2e-p1: a merge decision only links its task, so nothing is released and
        # without this the captain that opened it never learns the owner said yes
        t = self.task(assignee="impl", state="active")
        self.open(sid=SID_PM, category="merge", title="入れるか", links=[t["id"]])
        with mock.patch("yamato.seat.wake", return_value=("alive", {"shortId": "pppppppp"})):
            self.close("D-001", sid="h" * 36, on_behalf_of="owner", choice="merge する")
        [msg] = inbox.entries(self.shipdir, "pm")
        self.assertEqual(msg["from"], "owner")
        self.assertIn("D-001 が決まった (入れるか): owner の決定「merge する」。 結んだ項目: T-001。", msg["text"])
        self.assertEqual(inbox.entries(self.shipdir, "impl"), [])

    def test_the_opener_is_told_once_and_not_when_it_closed_it(self):
        t = self.task(assignee="impl", state="active")
        self.open(blocks=[t["id"]])                     # impl opens, decider pm
        self.close("D-001", sid=SID_PM)
        self.assertEqual(len(inbox.entries(self.shipdir, "impl")), 1)   # the release, not twice
        t2 = self.task(assignee="impl", state="active")
        self.open(sid=SID_PM, category="merge", links=[t2["id"]])
        self.close("D-002", sid=SID_PM, on_behalf_of="owner")
        self.assertFalse(any("D-002 が決まった" in m["text"] for m in inbox.entries(self.shipdir, "pm")))

    def test_only_the_last_blocker_releases_the_task(self):
        t = self.task(state="active")
        self.open(blocks=[t["id"]])
        self.open(blocks=[t["id"]])
        result, _ = self.close("D-001")
        self.assertEqual(result["unblocked"], [])
        m, _, _ = self.brd.read(t["id"])
        self.assertEqual((m["state"], m["blocked_on"]), ("blocked", ["D-002"]))
        result, _ = self.close("D-002")
        m, _, _ = self.brd.read(t["id"])
        self.assertEqual((m["state"], m["blocked_on"]), ("active", []))

    def test_decider_is_fixed_when_opened(self):
        self.open(category="design")    # pm
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("design:       { decider: pm,", "design:       { decider: owner,"))
        self.assertEqual(self.team()["decisions"]["design"]["decider"], "owner")
        result, _ = self.close("D-001", sid=SID_PM)
        self.assertTrue(result["by_decider"])
        self.assertEqual(result["meta"]["decider"], "pm")

    def test_closed_decisions_are_not_rewritten(self):
        self.open()
        self.close("D-001")
        with self.assertRaises(YamatoError):
            self.close("D-001")
        with self.assertRaises(YamatoError):
            self.brd.set("D-001", {}, note="後から", by="pm")
        with self.assertRaises(YamatoError):
            self.brd.set("D-001", {"title": "別"}, by="pm")
        d2, _ = self.open(supersedes="D-001")
        self.assertEqual(d2["supersedes"], "D-001")
        self.close(d2["id"])
        self.assertIn("覆したもの: D-001", (self.shipdir / "decisions/log.md").read_text())

    def test_board_archive_moves_closed_decisions_too(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("archive_on_done: true", "archive_on_done: false"))
        self.open()
        self.close("D-001")
        self.assertTrue((self.shipdir / "board/items/D-001.md").exists())
        brd = board.Board(self.shipdir, self.team())
        self.assertEqual(brd.archive(), ["D-001"])
        self.assertTrue((self.shipdir / "board/archive/D-001.md").exists())
        self.assertFalse((self.shipdir / "board/items/D-001.md").exists())

    def test_board_set_on_an_open_decision(self):
        self.open()
        self.brd.set("D-001", {"priority": "high"}, note="補足", by="impl")   # free fields and notes are fine
        for fields in ({"state": "done"}, {"decider": "owner"}, {"closed_by": "pm"}):
            with self.subTest(fields=fields), self.assertRaises(YamatoError):
                self.brd.set("D-001", fields, by="pm")

    def test_close_refusals(self):
        t = self.task()
        with self.assertRaises(YamatoError):
            self.close(t["id"])               # not a decision
        self.open()
        for kw in (dict(choice=" "), dict(reason="")):
            with self.subTest(kw=kw), self.assertRaises(YamatoError):
                self.close("D-001", **kw)


class ListTest(DecideTestCase):
    def test_list_filters(self):
        t = self.task()
        now = time.time()
        with self.as_seat(SID_IMPL):
            decide.open_decision(self.shipdir, self.team(), category="merge", title="古い", blocks=[t["id"]],
                                 now=now - 3 * 86400)
            decide.open_decision(self.shipdir, self.team(), category="design", title="新しい", now=now - 60)
            decide.open_decision(self.shipdir, self.team(), category="design", title="閉じる")
            decide.close_decision(self.shipdir, self.team(), "D-003", choice="x", reason="y")
        ids = lambda ms: [m["id"] for m in ms]
        self.assertEqual(ids(decide.waiting(self.brd)), ["D-001", "D-002"])
        self.assertEqual(ids(decide.waiting(self.brd, decider="owner")), ["D-001"])
        self.assertEqual(ids(decide.waiting(self.brd, stale="2d")), ["D-001"])
        self.assertEqual(ids(decide.waiting(self.brd, stale="30m", now=now)), ["D-001"])
        line = decide.format_decision(self.brd, decide.waiting(self.brd)[0])
        self.assertIn("D-001 [open] 古い", line)
        self.assertIn("merge → owner", line)
        self.assertIn("3d 待ち", line)
        self.assertIn("止めている: T-001", line)


class CliTest(DecideTestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_open_list_close_categories(self):
        ship = str(self.shipdir)
        self.task(assignee="impl", state="active")
        body = self.tmp / "body.md"
        body.write_text("## 背景\nb\n")
        code, out, _ = self.run_cli("decide", "open", ship, "--category", "design", "--title", "API の形",
                                    "--blocks", "T-001", "--body-file", str(body), "--urgent")
        self.assertEqual(code, 0)
        self.assertIn("開いた: D-001 (design → decider pm) API の形", out)
        self.assertIn("blocked にした: T-001", out)
        code, out, _ = self.run_cli("decide", "list", ship)
        self.assertIn("D-001 [open] [急ぎ] API の形", out)
        code, out, _ = self.run_cli("decide", "close", ship, "D-001", "--choice", "REST", "--reason", "単純",
                                    "--by", "pm")
        self.assertEqual(code, 0)
        self.assertIn("閉じた: D-001 決定=REST (決めた人 pm, 閉じた席 owner)", out)
        self.assertIn("止まりが解けた: T-001 [active]", out)
        code, out, _ = self.run_cli("decide", "list", ship)
        self.assertIn("(待ちの判断なし)", out)
        code, out, _ = self.run_cli("decide", "list", ship, "--all")
        self.assertIn("D-001 [done]", out)
        code, out, _ = self.run_cli("decide", "categories", ship)
        self.assertIn("merge: decider=owner — PR を main に入れる", out)
        code, _, err = self.run_cli("decide", "close", ship, "D-001", "--choice", "x", "--reason", "y")
        self.assertEqual(code, 1)
        self.assertIn("もう閉じている", err)


class EventsReviewNitsTest(DecideTestCase):
    """#7 review follow-ups."""

    def test_unserialisable_data_does_not_raise(self):
        with redirect_stderr(io.StringIO()) as err:
            self.assertIsNone(events.emit(self.shipdir, events.SEND, data={"x": object()}))
        self.assertIn("events.jsonl", err.getvalue())

    def test_now_zero_is_kept(self):
        self.assertEqual(events.emit(self.shipdir, events.SEND, now=0.0)["ts"], 0.0)

    def test_lines_without_a_numeric_ts_are_skipped(self):
        with open(events.path(self.shipdir), "a") as f:
            for ts in ('"yesterday"', "null", "true"):
                f.write(json.dumps({"kind": "x"})[:-1] + f', "ts": {ts}}}\n')
            f.write('{"kind": "y"}\n')
        events.emit(self.shipdir, "ok", now=5.0)
        self.assertEqual([e["kind"] for e in events.read(self.shipdir) if e["kind"] in ("x", "y", "ok")], ["ok"])

    def test_touch_does_not_create_an_unknown_seat(self):
        roster.touch(self.shipdir, "ghost", now=1.0)
        self.assertNotIn("ghost", roster.load(self.shipdir)["seats"])
        roster.touch(self.shipdir, "impl", now=2.0)
        self.assertEqual(roster.seat(self.shipdir, "impl")["lastActive"], 2.0)


class PrMergeRequiresTest(DecideTestCase):
    def test_merge_decision_opened_with_links_holds_the_merge(self):
        from yamato import pr

        t = self.task(assignee="impl", state="active")
        team = self.team()
        meta = {"id": t["id"], "pr": 1}
        team["git"]["merge_requires"] = ["decision"]
        d, _ = self.open(sid=SID_PM, category="merge", title="入れるか", links=[t["id"]])
        m, _, _ = self.brd.read(t["id"])
        self.assertEqual(m["state"], "active")          # --links does not block
        self.assertEqual(d["links"], [t["id"]])
        self.assertEqual(pr.unmet(self.shipdir, team, meta), ["decision: merge の判断 D-001 が閉じていない"])
        self.close("D-001", sid=SID_PM, on_behalf_of="owner", choice="入れる")
        self.assertEqual(pr.unmet(self.shipdir, team, meta), [])
        with self.assertRaises(YamatoError):
            self.open(links=["T-404"])


class DailyReportTest(DecideTestCase):
    def test_the_daily_report_lists_open_decisions_for_the_owner(self):
        from yamato import report

        self.open(sid=SID_PM, category="merge", title="入れるか",
                  body="## 背景\nx\n## 選択肢と推し\n- 入れる (推し)\n")
        self.open(category="design", title="pm が決める")          # decider pm: not the owner's
        self.open(sid=SID_PM, category="scope_change", title="閉じる")
        self.close("D-003", sid=SID_PM, on_behalf_of="owner")
        [line] = report.decision_lines(self.shipdir, self.team(), "2026-09-26", time.time())
        self.assertTrue(line.startswith("- D-001 入れるか (待ち "))
        self.assertIn("推し: 入れる (推し)", line)
