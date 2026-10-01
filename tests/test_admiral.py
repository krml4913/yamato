"""admiral の CLI (design-p1 §6) against tests/fake_claude.py."""
import contextlib
import io
import json
import subprocess
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import admiral, board, claude, cli, deadline, events, headless, inbox, procs, report, roster, seat, ship
from yamato.team import load_team
from yamato.util import YamatoError, load_registry, yamato_home


@contextlib.contextmanager
def posix_foreground():
    """``run_foreground`` の中だけ POSIX の枝 (execvp を呼ぶ) にする。Windows 機では差し替えた
    execvp が無視され、本物の ``claude attach`` を走らせてしまう (T-050)。"""
    real = procs.run_foreground

    def fg(argv, **kw):
        with mock.patch.object(procs, "is_windows", return_value=False):
            return real(argv, **kw)
    with mock.patch.object(procs, "run_foreground", fg):
        yield


class AdmiralTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        self.watchdogs = []
        p = mock.patch.object(seat, "spawn_watchdog", side_effect=lambda d, t: self.watchdogs.append(t))
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(seat, "_spawn_detached")
        self.spawn_mock = p2.start()
        self.addCleanup(p2.stop)

    def run_cmd(self, fn, *args, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf), posix_foreground():
            fn(*args, **kw)
        return buf.getvalue()

    def cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(list(argv))
        return rc, buf.getvalue()

    def bg_names(self):
        return [c["argv"][c["argv"].index("--name") + 1] for c in self.fake()["calls"]
                if "--bg" in c["argv"] and "--resume" not in c["argv"]]

    def alive(self, name):
        sid = roster.seat(self.shipdir, name).get("sessionId")
        return claude.is_alive(claude.find(sid)) if sid else False

    def stop_session(self, name):
        st = self.fake()
        for s in st["sessions"]:
            if s["sessionId"] == roster.seat(self.shipdir, name)["sessionId"]:
                s["pid"] = None
        self.fake_state.write_text(json.dumps(st), encoding="utf-8")

    def set_session(self, name, **fields):
        st = self.fake()
        for s in st["sessions"]:
            if s["sessionId"] == roster.seat(self.shipdir, name)["sessionId"]:
                s.update(fields)
        self.fake_state.write_text(json.dumps(st), encoding="utf-8")

    # --- up --seats ---

    def test_up_seats_wakes_the_named_seats_with_the_captain(self):
        rc, out = self.cli("up", str(self.shipdir), "--for", "1h", "--seats", "impl")
        self.assertEqual(rc, 0)
        self.assertEqual(self.bg_names(), ["t1.pm", "t1.impl"])
        self.assertIn("席 impl: 新しいシフトを起動した", out)

    def test_up_seats_rejects_an_unknown_seat_before_starting_anything(self):
        rc, _ = self.cli("up", str(self.shipdir), "--seats", "impl,nope")
        self.assertEqual(rc, 1)
        self.assertEqual(self.bg_names(), [])
        self.assertIsNone(deadline.read(self.shipdir))

    def add_headless_role(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text(encoding="utf-8").replace(
            "roles:\n", "roles:\n  researcher:\n    shift: headless\n    description: 調査担当\n", 1), encoding="utf-8")
        (self.shipdir / "roles" / "researcher.md").write_text("あなたは researcher です。\n", encoding="utf-8")
        seat.prepare(self.shipdir)

    def test_up_seats_starts_a_headless_seat(self):
        self.add_headless_role()
        with mock.patch.object(headless, "wake", return_value=("spawned", {})) as wake:
            rc, out = self.cli("up", str(self.shipdir), "--for", "1h", "--seats", "researcher")
        self.assertEqual(rc, 0)
        self.assertEqual(wake.call_args.args[2], "researcher")
        self.assertIn("席 researcher: headless のシフトを起動した", out)

    def test_up_seats_wakes_the_rest_when_one_seat_fails(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text(encoding="utf-8").replace("    count: 1 ", "    count: 3 ", 1), encoding="utf-8")
        real = seat.wake

        def wake(shipdir, team, s, reason="send"):
            if s == "impl-1":
                raise YamatoError("席 impl-1 の起動に失敗しました")
            return real(shipdir, team, s, reason)

        with mock.patch.object(seat, "wake", side_effect=wake):
            rc, out = self.cli("up", str(self.shipdir), "--for", "1h", "--seats", "impl-1,impl-2,impl-3")
        self.assertEqual(rc, 1)
        self.assertEqual(self.bg_names(), ["t1.pm", "t1.impl-2", "t1.impl-3"])
        self.assertIn("席 impl-1: 起動に失敗", out)
        self.assertIn("席 impl-3: 新しいシフトを起動した", out)
        self.assertEqual(deadline.phase(deadline.read(self.shipdir)), deadline.RUNNING)   # the ship is up

    def short_launch_check(self):
        for name, value in (("LAUNCH_SETTLE", 0), ("LAUNCH_CHECK_TIMEOUT", 0.05), ("LAUNCH_CHECK_POLL", 0.01)):
            p = mock.patch.object(claude, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_up_whose_captain_did_not_come_up_leaves_no_deadline(self):
        self.short_launch_check()
        self.set_fake_mode(session={"pid": None, "state": "failed"})
        rc, _ = self.cli("up", str(self.shipdir), "--for", "1h")
        self.assertEqual(rc, 1)
        self.assertIsNone(deadline.read(self.shipdir))   # no deadline left without its watchdog
        self.assertEqual(self.watchdogs, [])
        self.assertIn("停止中", admiral.ship_line("t1", self.shipdir, claude.by_session(claude.agents()), time.time()))

    def test_up_whose_captain_did_not_come_up_keeps_a_live_seats_deadline_watched(self):
        self.short_launch_check()
        self.cli("up", str(self.shipdir), "--for", "1h", "--seats", "impl")
        self.stop_session("pm")
        self.set_fake_mode(session={"pid": None, "state": "failed"})
        rc, _ = self.cli("up", str(self.shipdir), "--for", "1h")
        self.assertEqual(rc, 1)
        self.assertTrue(self.alive("impl"))
        dl = deadline.read(self.shipdir)
        self.assertEqual(self.watchdogs[-1], dl["token"])   # impl stays under a watched limit

    def test_up_without_seats_starts_only_the_captain(self):
        self.cli("up", str(self.shipdir))
        self.assertEqual(self.bg_names(), ["t1.pm"])

    # --- up の再呼び出しが deadline を巻き戻さないこと (D-015) ---

    def test_up_without_for_does_not_shrink_a_running_deadline(self):
        # 艦がすでに稼働中で、extend で team.yaml の time_limit (3h) よりずっと先まで
        # 延ばしてある。--for なしでもう一度 up (--seats で席を足すときの典型) を呼んでも、
        # 締切は time_limit の既定 (now + 3h) には巻き戻らない。
        self.run_cmd(seat.up, self.shipdir, "20m")
        self.run_cmd(admiral.extend, self.shipdir, "5h")
        before = deadline.read(self.shipdir)
        self.assertGreater(before["deadline"] - time.time(), 3 * 3600)   # time_limit を超えて延びている
        self.cli("up", str(self.shipdir), "--seats", "impl")
        after = deadline.read(self.shipdir)
        self.assertAlmostEqual(after["deadline"], before["deadline"], delta=2)
        self.assertAlmostEqual(after["graceUntil"], after["deadline"] + before["grace"], delta=2)

    def test_up_with_for_still_overwrites_explicitly(self):
        # --for を明示したときは、縮める意図を尊重して今までどおり上書きする。
        self.run_cmd(seat.up, self.shipdir, "2h")
        self.run_cmd(admiral.extend, self.shipdir, "3h")
        self.cli("up", str(self.shipdir), "--for", "10m")
        after = deadline.read(self.shipdir)
        self.assertAlmostEqual(after["deadline"], time.time() + 600, delta=2)

    def test_up_without_for_uses_the_team_time_limit_when_not_up(self):
        # 艦が稼働中でなければ (NOT_UP / OVER / FORCE)、--for なしはこれまでどおり
        # team.yaml の time_limit を使う。
        self.cli("up", str(self.shipdir))
        after = deadline.read(self.shipdir)
        self.assertAlmostEqual(after["deadline"], time.time() + self.team()["time_limit"], delta=2)

    # --- extend ---

    def test_extend_moves_deadline_and_grace(self):
        self.run_cmd(seat.up, self.shipdir, "20m")
        before = deadline.read(self.shipdir)
        out = self.run_cmd(admiral.extend, self.shipdir, "1h")
        after = deadline.read(self.shipdir)
        self.assertAlmostEqual(after["deadline"], before["deadline"] + 3600, delta=1)
        self.assertAlmostEqual(after["graceUntil"], after["deadline"] + before["grace"], delta=1)
        self.assertEqual(after["token"], before["token"])
        self.assertEqual(self.watchdogs[-1], before["token"])
        self.assertIn("延ばした", out)

    def test_extend_after_the_deadline_counts_from_now_and_resets_wrapup_notices(self):
        self.run_cmd(seat.up, self.shipdir, "20m")
        self.run_cmd(seat.down, self.shipdir, False)   # deadline = now
        notice = self.shipdir / ".runtime" / "wrapup-pm.json"
        notice.write_text(json.dumps({"shiftNo": 1, "count": 3}), encoding="utf-8")
        out = self.run_cmd(admiral.extend, self.shipdir, "30m")
        dl = deadline.read(self.shipdir)
        self.assertEqual(deadline.phase(dl), deadline.RUNNING)
        self.assertAlmostEqual(dl["deadline"], time.time() + 1800, delta=5)
        self.assertFalse(notice.exists())
        self.assertIn("今から数えた", out)

    def test_extend_refuses_a_ship_that_is_not_up(self):
        with self.assertRaises(YamatoError):
            admiral.extend(self.shipdir, "1h")

    def test_watchdog_reads_the_extended_deadline(self):
        self.run_cmd(seat.up, self.shipdir, "20m")
        dl = deadline.read(self.shipdir)
        now = time.time()
        deadline.write_raw(self.shipdir, {**dl, "deadline": now + 0.2, "graceUntil": now + 0.3, "grace": 0.1})
        forced = []
        with mock.patch.object(seat, "WATCHDOG_POLL", 0.05), mock.patch.object(seat, "WATCHDOG_MIN_SLEEP", 0.01), \
                mock.patch.object(seat, "enforce", side_effect=lambda *a: forced.append(1) or []):
            t = threading.Thread(target=seat.watchdog, args=(self.shipdir, dl["token"]))
            t.start()
            self.run_cmd(admiral.extend, self.shipdir, "1h")
            time.sleep(0.3)                        # several polls past the old graceUntil
            self.assertTrue(t.is_alive())          # still waiting on the new deadline
            self.assertEqual(forced, [])           # the old graceUntil passed without a force stop
            deadline.write_raw(self.shipdir, {**deadline.read(self.shipdir), "token": "other"})
            t.join(5)
        self.assertFalse(t.is_alive())

    def test_watchdog_forces_a_stuck_stopping_seat(self):
        """T-012: the watchdog's own loop notices a seat stuck `stopping` too, not just
        `status` -- it does not need a human to run `status` for the fix to kick in."""
        self.run_cmd(seat.up, self.shipdir, "20m")
        rec = roster.seat(self.shipdir, "pm")
        roster.mark_stopping(self.shipdir, "pm", handoff_written=True,
                             now=time.time() - 999999)   # long past any STOPPING_STUCK_AFTER
        dl = deadline.read(self.shipdir)
        # STOPPING_STUCK_AFTER stays at its real (several-minute) default: `ago` above already
        # clears it on the very first poll, and the default is comfortably longer than this
        # test's whole run, so the "not too often" guard reliably keeps this to one retry
        with mock.patch.object(seat, "WATCHDOG_POLL", 0.02), mock.patch.object(seat, "WATCHDOG_MIN_SLEEP", 0.01):
            t = threading.Thread(target=seat.watchdog, args=(self.shipdir, dl["token"]))
            t.start()
            time.sleep(0.2)                        # several polls
            self.assertTrue(t.is_alive())           # still watching the (unchanged) deadline
            self.spawn_mock.assert_called()
            [(args, kwargs)] = [(c.args[0], c.kwargs) for c in self.spawn_mock.call_args_list]
            self.assertEqual(kwargs.get("cwd"), str(self.shipdir))
            self.assertIn("sleep 0; ", args[2])
            self.assertTrue(roster.seat(self.shipdir, "pm")["restopAttemptAt"])
            [ev] = events.read(self.shipdir, kinds=events.STOPPING_STUCK)
            self.assertEqual(ev["seat"], "pm")
            deadline.write_raw(self.shipdir, {**deadline.read(self.shipdir), "token": "other"})   # let it exit
            t.join(5)
        self.assertFalse(t.is_alive())

    # --- halt ---

    def test_halt_stops_everything_without_grace_and_runs_the_report_safety_net(self):
        self.cli("up", str(self.shipdir), "--seats", "impl")
        out = self.run_cmd(admiral.halt, self.shipdir)
        self.assertFalse(self.alive("pm"))
        self.assertFalse(self.alive("impl"))
        self.assertEqual(deadline.phase(deadline.read(self.shipdir)), deadline.FORCE)
        reasons = [e["data"]["reason"] for e in events.read(self.shipdir, kinds=(events.FORCE_STOP,))]
        self.assertEqual(sorted(reasons), ["halt", "halt"])
        self.assertTrue(report.report_path(self.shipdir, time.strftime("%Y-%m-%d")).exists())
        self.assertIn("緊急停止", out)

    def test_halt_with_nothing_alive_still_makes_the_report(self):
        self.run_cmd(seat.up, self.shipdir, "20m")
        self.stop_session("pm")
        self.run_cmd(admiral.halt, self.shipdir)
        self.assertTrue(report.report_path(self.shipdir, time.strftime("%Y-%m-%d")).exists())

    # --- red seats / status ---

    def test_status_flags_a_live_seat_idle_longer_than_stale_after(self):
        self.run_cmd(seat.up, self.shipdir, "1h")
        with mock.patch.object(seat, "_last_active", return_value=time.time() - 25 * 60):
            out = self.run_cmd(seat.status, self.shipdir)
        self.assertIn("!!! pm: 25m", out)
        self.assertIn("動いていない", out)

    def test_stale_after_is_set_in_team_yaml(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text(encoding="utf-8").replace("stale_after: 20m", "stale_after: 30m"), encoding="utf-8")
        self.run_cmd(seat.up, self.shipdir, "1h")
        with mock.patch.object(seat, "_last_active", return_value=time.time() - 25 * 60):
            out = self.run_cmd(seat.status, self.shipdir)
        self.assertNotIn("!!!", out)

    def test_status_flags_failed_state(self):
        self.run_cmd(seat.up, self.shipdir, "1h")
        self.set_session("pm", state="failed")
        out = self.run_cmd(seat.status, self.shipdir)
        self.assertIn("!!! pm: API エラー", out)

    def test_status_flags_what_the_seat_waits_on(self):
        """verify-p0-c Q5: status waiting names its waitingFor; blocked while idle is a question
        to a human; blocked while busy is a Monitor wait (normal)."""
        self.run_cmd(seat.up, self.shipdir, "1h")
        self.set_session("pm", status="waiting", waitingFor="dialog open", state="blocked")
        out = self.run_cmd(seat.status, self.shipdir)
        self.assertIn("!!! pm: 詰まり: dialog open で止まっている", out)
        self.assertIn("state=blocked", out)
        self.assertNotIn("返事待ち", out)
        self.set_session("pm", status="idle", waitingFor=None, state="blocked")
        self.assertIn("!!! pm: 人間の返事待ちの疑い", self.run_cmd(seat.status, self.shipdir))
        self.set_session("pm", status="busy", state="blocked")
        self.assertNotIn("!!!", self.run_cmd(seat.status, self.shipdir))
        self.set_session("pm", pid=None, state="blocked")   # liveness is the pid, not state
        self.assertNotIn("!!!", self.run_cmd(seat.status, self.shipdir))

    def test_status_flags_a_live_per_task_seat_without_an_active_item(self):
        # design-drift #11 / D-019
        self.cli("up", str(self.shipdir), "--for", "1h")
        self.cli("send", str(self.shipdir), "impl", "T-001")   # impl comes up and stays alive
        rc, out = self.cli("status", str(self.shipdir))
        self.assertIn("!!! impl: per_task の席が生きているのに担当", out)

    def test_status_does_not_flag_a_per_task_seat_with_an_active_item(self):
        self.cli("up", str(self.shipdir), "--for", "1h")
        self.cli("send", str(self.shipdir), "impl", "T-001")
        board.Board(self.shipdir, self.team()).add("T-001", {"assignee": "impl", "state": "active"}, by="pm")
        rc, out = self.cli("status", str(self.shipdir))
        self.assertNotIn("!!!", out)

    def test_ships_counts_a_live_per_task_orphan_as_red(self):
        self.cli("up", str(self.shipdir), "--for", "1h")
        self.cli("send", str(self.shipdir), "impl", "T-001")
        rc, out = self.cli("ships")
        self.assertIn("赤 1 (impl)", out)

    def test_a_stopped_seat_is_not_red(self):
        team = self.team()
        self.assertEqual(admiral.red_flags(team, "pm", {"lastActive": 1}, None, time.time()), [])

    # --- ships ---

    def test_ships_one_line_per_ship(self):
        self.cli("up", str(self.shipdir), "--for", "1h")
        (self.shipdir / "board" / "items" / "D-1.md").write_text(
            "---\nid: D-1\ntitle: 認証\nkind: decision\nstate: open\ndecider: owner\n---\n", encoding="utf-8")
        (self.shipdir / "board" / "items" / "D-2.md").write_text(
            "---\nid: D-2\ntitle: 内部\nkind: decision\nstate: open\ndecider: pm\n---\n", encoding="utf-8")
        with open(self.shipdir / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "total_tokens": 12000}) + "\n")
            f.write(json.dumps({"ts": time.time() - 3 * 86400, "total_tokens": 99000}) + "\n")
        (self.shipdir / "reports" / "daily").mkdir(parents=True)
        (self.shipdir / "reports" / "daily" / "2026-09-25.md").write_text("# x\n", encoding="utf-8")
        self.set_fake_mode(waitingFor="permission prompt")
        self.cli("send", str(self.shipdir), "impl", "go")   # impl comes up waiting on a prompt

        from yamato import ship

        ship.create("t2", str(self.workspace), None, "dev")
        rc, out = self.cli("ships")
        self.assertEqual(rc, 0)
        l1, l2 = out.strip().splitlines()
        self.assertTrue(l1.startswith("t1"))
        self.assertIn("稼働中 残り", l1)
        self.assertIn("captain pm: 生", l1)
        self.assertIn("赤 1 (impl)", l1)
        self.assertIn("判断待ち 1", l1)
        self.assertIn("今日 12k tok", l1)
        self.assertIn("日報 2026-09-25", l1)
        self.assertTrue(l2.startswith("t2"))
        self.assertIn("停止中", l2)
        self.assertIn("日報 -", l2)

    # --- T-020: a ship folder created without registering it (the admiral's _admiral/) ---

    def test_ships_excludes_a_folder_created_without_registering(self):
        admdir, _ = ship.create("admiral", str(self.workspace), str(yamato_home() / "_admiral"),
                                "dev", register=False)
        self.assertEqual(admdir, yamato_home() / "_admiral")
        # 同じ形の艦フォルダはできる (team.yaml、席の log/inbox) が、登録も一覧もされない
        self.assertTrue((admdir / "team.yaml").is_file())
        self.assertTrue((admdir / "seats" / "pm" / "log").is_dir())
        self.assertTrue((admdir / "seats" / "pm" / "inbox.jsonl").is_file())
        self.assertNotIn("admiral", load_registry())
        found = admiral.all_ships()
        self.assertIn("t1", found)
        self.assertNotIn("_admiral", found)
        self.assertNotIn("admiral", found)
        rc, out = self.cli("ships")
        self.assertEqual(rc, 0)
        self.assertNotIn("_admiral", out)
        # still reachable directly by its folder name (resolve_ship's plain fallback)
        rc, out = self.cli("status", "_admiral")
        self.assertEqual(rc, 0)
        self.assertIn("pm", out)

    # --- talk ---

    def test_talk_attaches_to_the_live_captain_by_default(self):
        self.run_cmd(seat.up, self.shipdir, "1h")
        calls = []
        self.run_cmd(admiral.talk, self.shipdir, None, execvp=lambda f, a: calls.append(a))
        short = roster.seat(self.shipdir, "pm")["shortId"]
        self.assertEqual(calls, [[*claude.claude_cmd(), "attach", short]])
        self.assertEqual(self.bg_names(), ["t1.pm"])

    def test_talk_default_comes_from_team_yaml(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text(encoding="utf-8").replace("talk_default: pm", "talk_default: impl"), encoding="utf-8")
        seat.prepare(self.shipdir)
        self.run_cmd(seat.up, self.shipdir, "1h")
        calls = []
        self.run_cmd(admiral.talk, self.shipdir, None, execvp=lambda f, a: calls.append(a))
        self.assertEqual(calls[0][2], roster.seat(self.shipdir, "impl")["shortId"])

    def test_talk_default_must_be_a_seat(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text(encoding="utf-8").replace("talk_default: pm", "talk_default: nope"), encoding="utf-8")
        with self.assertRaises(YamatoError):
            self.team()

    def test_talk_wakes_a_stopped_seat_before_attaching(self):
        self.run_cmd(seat.up, self.shipdir, "1h")
        sid = roster.seat(self.shipdir, "pm")["sessionId"]
        self.stop_session("pm")
        calls = []
        self.run_cmd(admiral.talk, self.shipdir, "pm", execvp=lambda f, a: calls.append(a))
        resumes = [c for c in self.fake()["calls"] if "--resume" in c["argv"]]
        self.assertEqual(resumes[0]["argv"][resumes[0]["argv"].index("--resume") + 1], sid)
        self.assertEqual(calls, [[*claude.claude_cmd(), "attach", sid[:8]]])
        self.assertIn("talk", inbox.unread(self.shipdir, "pm")[-1]["text"])

    def test_talk_does_not_attach_when_the_ship_is_not_up(self):
        calls = []
        with self.assertRaises(YamatoError):
            self.run_cmd(admiral.talk, self.shipdir, "pm", execvp=lambda f, a: calls.append(a))
        self.assertEqual(calls, [])
        self.assertEqual(self.bg_names(), [])
        self.assertEqual(inbox.entries(self.shipdir, "pm"), [])

    def test_talk_refuses_a_headless_seat_without_waking_it(self):
        self.add_headless_role()
        self.run_cmd(seat.up, self.shipdir, "1h")
        calls = []
        with mock.patch.object(headless, "wake") as wake, self.assertRaises(YamatoError) as cm:
            self.run_cmd(admiral.talk, self.shipdir, "researcher", execvp=lambda f, a: calls.append(a))
        self.assertIn("headless", str(cm.exception))
        wake.assert_not_called()
        self.assertEqual(calls, [])
        self.assertEqual(inbox.entries(self.shipdir, "researcher"), [])

    # --- ship create: trust on the main repo (V6) ---

    def test_trust_of_a_linked_worktree_comes_from_the_main_repo(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q", str(repo)], check=True)
        subprocess.run([*git, "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        wt = self.tmp / "wt"
        subprocess.run([*git, "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
        self.assertFalse(claude.is_trusted(wt))
        (self.config / ".claude.json").write_text(json.dumps(
            {"projects": {str(repo.resolve()): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
        self.assertTrue(claude.is_trusted(wt))
        from yamato import ship

        _, warnings = ship.create("t3", str(wt), None, "dev")
        self.assertFalse([w for w in warnings if "trust" in w])

    def test_untrusted_worktree_message_names_the_main_repo(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q", str(repo)], check=True)
        subprocess.run([*git, "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        wt = self.tmp / "wt"
        subprocess.run([*git, "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
        self.assertIn(f"cd {repo.resolve()} && claude", claude.untrusted_message(wt))


# --- T-021: `yamato admiral` (attach/wake/stop, D-011) ------------------------------------
# templates/admiral/ (T-023) is now the real thing (its own shape is tests/test_admiral_template.py's
# job); these tests use it as-is and only exercise the `yamato admiral` command built on top of it.


class AdmiralUpCommandTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        self.watchdogs = []
        p = mock.patch.object(seat, "spawn_watchdog", side_effect=lambda d, t: self.watchdogs.append(t))
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(seat, "_spawn_detached")
        self.spawn_mock = p2.start()
        self.addCleanup(p2.stop)
        # the admiral's workspace is `_admiral/` itself (template `workspace: .`): trust it
        # too, the same way ShipTestCase already trusts `self.workspace` for "t1"
        cc = json.loads((self.config / ".claude.json").read_text(encoding="utf-8"))
        cc["projects"][str(self.admdir())] = {"hasTrustDialogAccepted": True}
        (self.config / ".claude.json").write_text(json.dumps(cc), encoding="utf-8")

    def admdir(self):
        return yamato_home() / "_admiral"

    def run_cmd(self, fn, *args, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf), posix_foreground():
            fn(*args, **kw)
        return buf.getvalue()

    def cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(list(argv))
        return rc, buf.getvalue()

    def alive_seat(self, shipdir, name):
        sid = roster.seat(shipdir, name).get("sessionId")
        return claude.is_alive(claude.find(sid)) if sid else False

    def stop_admiral(self, admdir):
        sid = roster.seat(admdir, "admiral")["sessionId"]
        st = self.fake()
        for s in st["sessions"]:
            if s["sessionId"] == sid:
                s["pid"] = None
        self.fake_state.write_text(json.dumps(st), encoding="utf-8")

    # --- ensure_admiral: built once, unregistered ---

    def test_ensure_admiral_builds_an_unregistered_ship_once(self):
        path = admiral.ensure_admiral()
        self.assertEqual(path, self.admdir())
        self.assertTrue((path / "team.yaml").is_file())
        self.assertTrue((path / "roles" / "admiral.md").is_file())
        self.assertNotIn("yamato", load_registry())
        self.assertNotIn("_admiral", load_registry())
        found = admiral.all_ships()
        self.assertIn("t1", found)
        self.assertNotIn("_admiral", found)
        # team.yaml's `name` came from ensure_admiral, not the caller: session_name() reads
        # "yamato.admiral" (the spec'd session name) off it
        self.assertEqual(load_team(path)["name"], "yamato")
        self.assertEqual(seat.session_name(load_team(path), "admiral"), "yamato.admiral")
        # idempotent: already there, so a second call must not try (ship.create) to rebuild it
        with mock.patch.object(ship, "create") as create:
            self.assertEqual(admiral.ensure_admiral(), path)
        create.assert_not_called()

    # --- attach / wake ---

    def test_admiral_wakes_and_attaches_with_remote_control_and_model(self):
        calls = []
        out = self.run_cmd(admiral.admiral_talk, execvp=lambda f, a: calls.append(a))
        admdir = self.admdir()
        rec = roster.seat(admdir, "admiral")
        self.assertEqual(rec["state"], roster.ON_SHIFT)
        self.assertEqual(calls, [[*claude.claude_cmd(), "attach", rec["shortId"]]])
        self.assertIn("claude attach", out)
        launched = [c for c in self.fake()["calls"] if "--bg" in c["argv"]][-1]["argv"]
        self.assertIn("--remote-control", launched)
        self.assertEqual(launched[launched.index("--model") + 1], "opus")
        self.assertEqual(launched[launched.index("--name") + 1], "yamato.admiral")

    def test_admiral_attaches_to_the_already_alive_session_without_relaunching(self):
        self.run_cmd(admiral.admiral_talk, execvp=lambda f, a: None)
        admdir = self.admdir()
        before = roster.seat(admdir, "admiral")["sessionId"]
        calls = []
        self.run_cmd(admiral.admiral_talk, execvp=lambda f, a: calls.append(a))
        self.assertEqual(roster.seat(admdir, "admiral")["sessionId"], before)
        self.assertEqual(len([c for c in self.fake()["calls"] if "--bg" in c["argv"]]), 1)
        self.assertEqual(calls, [[*claude.claude_cmd(), "attach", roster.seat(admdir, "admiral")["shortId"]]])

    def test_admiral_wakes_a_stopped_seat_even_with_no_deadline_ever(self):
        # D-013: there is no `up` for the admiral, so talk()'s usual "艦は稼働時間の外" gate
        # (only in bounds while a deadline says RUNNING) must not apply to it (T-021)
        self.run_cmd(admiral.admiral_talk, execvp=lambda f, a: None)
        admdir = self.admdir()
        self.assertIsNone(deadline.read(admdir))
        self.stop_admiral(admdir)
        calls = []
        self.run_cmd(admiral.admiral_talk, execvp=lambda f, a: calls.append(a))
        resumes = [c for c in self.fake()["calls"] if "--resume" in c["argv"]]
        self.assertTrue(resumes)
        self.assertEqual(calls[0][1], "attach")

    def test_wake_admiral_wakes_a_stopped_seat_without_attaching(self):
        # view の admiral ペインは view attach なので、起こすのは wake_admiral (execvp しない)
        with mock.patch("os.execvp") as ex:
            self.run_cmd(admiral.wake_admiral)
            ex.assert_not_called()
        admdir = self.admdir()
        self.assertEqual(len([c for c in self.fake()["calls"] if "--bg" in c["argv"]]), 1)
        self.assertTrue(self.alive_seat(admdir, "admiral"))
        self.stop_admiral(admdir)
        self.assertFalse(self.alive_seat(admdir, "admiral"))
        self.run_cmd(admiral.wake_admiral)
        self.assertTrue([c for c in self.fake()["calls"] if "--resume" in c["argv"]])
        self.assertTrue(self.alive_seat(admdir, "admiral"))

    def test_wake_admiral_does_nothing_when_it_is_alive(self):
        self.run_cmd(admiral.wake_admiral)
        admdir = self.admdir()
        before = (roster.seat(admdir, "admiral")["sessionId"], len(self.fake()["calls"]))
        self.run_cmd(admiral.wake_admiral)
        self.assertEqual(roster.seat(admdir, "admiral")["sessionId"], before[0])
        self.assertEqual(len([c for c in self.fake()["calls"] if "--bg" in c["argv"]]), 1)
        self.assertEqual([c for c in self.fake()["calls"] if "--resume" in c["argv"]], [])

    # --- stop (--stop / --force) ---

    def test_admiral_stop_before_it_ever_existed_is_a_clear_error(self):
        with self.assertRaises(YamatoError):
            self.run_cmd(admiral.admiral_stop, False)

    def test_admiral_stop_without_a_live_session_is_a_no_op(self):
        admiral.ensure_admiral()
        out = self.run_cmd(admiral.admiral_stop, False)
        self.assertIn("すでに止まっている", out)

    def test_admiral_stop_sends_the_note_and_does_not_block(self):
        self.run_cmd(admiral.admiral_talk, execvp=lambda f, a: None)
        admdir = self.admdir()
        out = self.run_cmd(admiral.admiral_stop, False)
        self.assertIn("引き継ぎ", inbox.unread(admdir, "admiral")[-1]["text"])
        self.assertIn("--force", out)
        self.assertTrue(self.alive_seat(admdir, "admiral"))

    def test_admiral_stop_force_waits_then_accepts_a_voluntary_seat_stop(self):
        self.run_cmd(admiral.admiral_talk, execvp=lambda f, a: None)
        admdir = self.admdir()
        sid = roster.seat(admdir, "admiral")["sessionId"]

        def voluntary_stop(_sid, timeout):
            self.stop_admiral(admdir)
            return True

        with mock.patch.object(claude, "wait_gone", side_effect=voluntary_stop) as wg:
            out = self.run_cmd(admiral.admiral_stop, True)
        wg.assert_called_once_with(sid, timeout=admiral.ADMIRAL_STOP_WAIT)
        self.assertIn("自分で終業した", out)

    def test_admiral_stop_force_stops_it_when_it_does_not_stop_itself(self):
        self.run_cmd(admiral.admiral_talk, execvp=lambda f, a: None)
        admdir = self.admdir()
        with mock.patch.object(claude, "wait_gone", return_value=False):
            out = self.run_cmd(admiral.admiral_stop, True)
        self.assertIn("強制停止した: admiral", out)
        self.assertFalse(self.alive_seat(admdir, "admiral"))
        self.assertIn("force_stop", (admdir / "events.jsonl").read_text(encoding="utf-8"))

    # --- rotate: 艦の席と同じ roles.<role>.rotate が効く (T-021 の完了条件 4) ---

    def test_rotate_marks_a_stopped_admiral_for_a_fresh_shift(self):
        self.run_cmd(admiral.admiral_talk, execvp=lambda f, a: None)
        admdir = self.admdir()
        self.stop_admiral(admdir)
        rc, out = self.cli("rotate", str(admdir), "admiral")
        self.assertEqual(rc, 0)
        self.assertTrue(roster.seat(admdir, "admiral").get("rotateRequested"))

    # --- seat-stop: 艦の席と同じに使える (T-021 の完了条件 3) ---

    def test_seat_stop_works_on_the_admiral_like_any_seat(self):
        self.run_cmd(admiral.admiral_talk, execvp=lambda f, a: None)
        admdir = self.admdir()
        sid = roster.seat(admdir, "admiral")["sessionId"]
        (admdir / "seats" / "admiral" / "handoff.md").write_text("引き継ぎ\n", encoding="utf-8")
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": sid}):
            rc, out = self.cli("seat-stop", str(admdir), "admiral", "--delivered")
        self.assertEqual(rc, 0)
        self.assertIn("終業を受け付けた", out)
        self.assertEqual(roster.seat(admdir, "admiral")["state"], roster.STOPPING)

    # --- CLI wiring (`yamato admiral` / `--stop` / `--force`) ---
    # admiral_talk / admiral_stop are exercised directly above; here only cli.py's own
    # dispatch and the --force-needs---stop guard (execvp is never safe to actually call)

    def test_cli_admiral_bare_calls_admiral_talk(self):
        with mock.patch.object(admiral, "admiral_talk", return_value=0) as at:
            rc, _ = self.cli("admiral")
        self.assertEqual(rc, 0)
        at.assert_called_once_with()

    def test_cli_admiral_stop_calls_admiral_stop(self):
        with mock.patch.object(admiral, "admiral_stop", return_value=0) as astop:
            rc, _ = self.cli("admiral", "--stop")
        self.assertEqual(rc, 0)
        astop.assert_called_once_with(False)

    def test_cli_admiral_stop_force_calls_admiral_stop_with_force(self):
        with mock.patch.object(admiral, "admiral_stop", return_value=0) as astop:
            rc, _ = self.cli("admiral", "--stop", "--force")
        self.assertEqual(rc, 0)
        astop.assert_called_once_with(True)

    def test_cli_admiral_force_without_stop_is_refused(self):
        rc, _ = self.cli("admiral", "--force")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
