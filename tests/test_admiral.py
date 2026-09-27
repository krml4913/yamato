"""admiral の CLI (design-p1 §6) against tests/fake_claude.py."""
import io
import json
import subprocess
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import admiral, board, claude, cli, deadline, events, headless, inbox, report, roster, seat
from yamato.util import YamatoError


class AdmiralTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        self.watchdogs = []
        p = mock.patch.object(seat, "spawn_watchdog", side_effect=lambda d, t: self.watchdogs.append(t))
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(seat, "_spawn_detached")
        p2.start()
        self.addCleanup(p2.stop)

    def run_cmd(self, fn, *args, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
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
        self.fake_state.write_text(json.dumps(st))

    def set_session(self, name, **fields):
        st = self.fake()
        for s in st["sessions"]:
            if s["sessionId"] == roster.seat(self.shipdir, name)["sessionId"]:
                s.update(fields)
        self.fake_state.write_text(json.dumps(st))

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
        ty.write_text(ty.read_text().replace(
            "roles:\n", "roles:\n  researcher:\n    shift: headless\n    description: 調査担当\n", 1))
        (self.shipdir / "roles" / "researcher.md").write_text("あなたは researcher です。\n")
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
        ty.write_text(ty.read_text().replace("    count: 1 ", "    count: 3 ", 1))
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
        notice.write_text(json.dumps({"shiftNo": 1, "count": 3}))
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
        ty.write_text(ty.read_text().replace("stale_after: 20m", "stale_after: 30m"))
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
            "---\nid: D-1\ntitle: 認証\nkind: decision\nstate: open\ndecider: owner\n---\n")
        (self.shipdir / "board" / "items" / "D-2.md").write_text(
            "---\nid: D-2\ntitle: 内部\nkind: decision\nstate: open\ndecider: pm\n---\n")
        with open(self.shipdir / "usage.jsonl", "a") as f:
            f.write(json.dumps({"ts": time.time(), "total_tokens": 12000}) + "\n")
            f.write(json.dumps({"ts": time.time() - 3 * 86400, "total_tokens": 99000}) + "\n")
        (self.shipdir / "reports" / "daily").mkdir(parents=True)
        (self.shipdir / "reports" / "daily" / "2026-09-25.md").write_text("# x\n")
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

    # --- talk ---

    def test_talk_attaches_to_the_live_captain_by_default(self):
        self.run_cmd(seat.up, self.shipdir, "1h")
        calls = []
        self.run_cmd(admiral.talk, self.shipdir, None, execvp=lambda f, a: calls.append(a))
        short = roster.seat(self.shipdir, "pm")["shortId"]
        self.assertEqual(calls, [[claude.claude_bin(), "attach", short]])
        self.assertEqual(self.bg_names(), ["t1.pm"])

    def test_talk_default_comes_from_team_yaml(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("talk_default: pm", "talk_default: impl"))
        seat.prepare(self.shipdir)
        self.run_cmd(seat.up, self.shipdir, "1h")
        calls = []
        self.run_cmd(admiral.talk, self.shipdir, None, execvp=lambda f, a: calls.append(a))
        self.assertEqual(calls[0][2], roster.seat(self.shipdir, "impl")["shortId"])

    def test_talk_default_must_be_a_seat(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("talk_default: pm", "talk_default: nope"))
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
        self.assertEqual(calls, [[claude.claude_bin(), "attach", sid[:8]]])
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
            {"projects": {str(repo.resolve()): {"hasTrustDialogAccepted": True}}}))
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


if __name__ == "__main__":
    unittest.main()
