"""W3: process OS differences on Windows (T-042, work/requirements/windows-w3.md).

Windows is never available here, so the Windows branches run with ``procs.is_windows`` /
``procs._kernel32`` replaced, asserting which API gets called (the real check is W5).
The four W0 [6] cases for ``pid_alive`` (alive / finished / nonexistent / self) are
answered by a fake kernel32."""
import ctypes
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import admiral, claude, headless, procs, roster


class FakeKernel32:
    """OpenProcess / GetExitCodeProcess / GetProcessTimes over a table {pid: exit code or None}."""

    def __init__(self, table, denied=(), created=None):
        self.table, self.denied, self.created = table, set(denied), created or {}
        self.closed = []
        self.opened_with = []

    def OpenProcess(self, access, inherit, pid):
        self.opened_with.append(access)
        if pid in self.denied:
            self._err = 5
            return 0
        if pid not in self.table:
            self._err = 87
            return 0
        return pid + 1000

    def GetExitCodeProcess(self, handle, ref):
        code = self.table[handle - 1000]
        ref._obj.value = procs.STILL_ACTIVE if code is None else code
        return 1

    def GetProcessTimes(self, handle, c, e, k, u):
        v = self.created.get(handle - 1000)
        if v is None:
            return 0
        c._obj.dwHighDateTime, c._obj.dwLowDateTime = v >> 32, v & 0xFFFFFFFF
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)


def windows(k32=None):
    """Patch procs into its Windows branch (os.name itself stays, pathlib needs it)."""
    patches = [mock.patch.object(procs, "is_windows", return_value=True)]
    if k32 is not None:
        patches += [mock.patch.object(procs, "_kernel32", return_value=k32),
                    mock.patch.object(ctypes, "get_last_error", lambda: getattr(k32, "_err", 0), create=True)]
    return patches


class WinCase(unittest.TestCase):
    def win(self, k32=None):
        for p in windows(k32):
            p.start()
            self.addCleanup(p.stop)


def posix_patches():
    """The POSIX branches run on a Windows machine too: is_windows is False and SIGKILL exists
    (Windows' signal module has none)."""
    return [mock.patch.object(procs, "is_windows", return_value=False),
            mock.patch.object(signal, "SIGKILL", 9, create=True)]


class PosixCase(unittest.TestCase):
    def setUp(self):
        for p in posix_patches():
            p.start()
            self.addCleanup(p.stop)


class PidAliveWindowsTest(WinCase):
    def test_the_four_w0_cases(self):
        k32 = FakeKernel32({100: None, 200: 3, os.getpid(): None})   # 生きている / 終わった / (300 は無い) / 自分
        self.win(k32)
        self.assertTrue(procs.pid_alive(100))
        self.assertFalse(procs.pid_alive(200))
        self.assertFalse(procs.pid_alive(300))
        self.assertTrue(procs.pid_alive(os.getpid()))
        self.assertEqual(set(k32.opened_with), {procs.PROCESS_QUERY_LIMITED_INFORMATION})
        self.assertEqual(len(k32.closed), 3)   # 開けた 3 つは全部閉じた

    def test_access_denied_means_it_exists(self):
        self.win(FakeKernel32({}, denied=[400]))
        self.assertTrue(procs.pid_alive(400))

    def test_never_calls_os_kill(self):
        self.win(FakeKernel32({100: None}))
        with mock.patch.object(os, "kill") as kill:
            procs.pid_alive(100)
        kill.assert_not_called()

    def test_bad_pids(self):
        self.win(FakeKernel32({}))
        for bad in (None, "", "x", 0, -1):
            self.assertFalse(procs.pid_alive(bad))

    def test_start_time(self):
        self.win(FakeKernel32({100: None}, created={100: (0x01D0 << 32) | 0x1234}))
        self.assertEqual(procs.start_time(100), (0x01D0 << 32) | 0x1234)
        self.assertIsNone(procs.start_time(999))


class PidAlivePosixTest(PosixCase):
    def test_posix_uses_kill_zero(self):
        with mock.patch.object(os, "kill", return_value=None):
            self.assertTrue(procs.pid_alive(os.getpid()))
        with mock.patch.object(os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(procs.pid_alive(4242))
        with mock.patch.object(os, "kill", side_effect=PermissionError):
            self.assertTrue(procs.pid_alive(4242))
        self.assertIsNone(procs.start_time(os.getpid()))
        self.assertEqual(procs.group_kwargs(), {})


class SpawnDetachedTest(WinCase):
    def test_posix_is_nohup_in_a_new_session(self):
        for p in posix_patches():
            p.start()
            self.addCleanup(p.stop)
        with mock.patch.object(subprocess, "Popen") as popen:
            procs.spawn_detached(["a", "b"], cwd="/x", env={"K": "v"})
        args, kw = popen.call_args
        self.assertEqual(args[0], ["nohup", "a", "b"])
        self.assertTrue(kw["start_new_session"])
        self.assertNotIn("creationflags", kw)
        self.assertEqual((kw["cwd"], kw["env"], kw["stdin"]), ("/x", {"K": "v"}, subprocess.DEVNULL))

    def test_windows_is_a_new_group_without_a_window_and_without_nohup(self):
        self.win()
        with mock.patch.object(subprocess, "Popen") as popen:
            procs.spawn_detached(["a", "b"])
        args, kw = popen.call_args
        self.assertEqual(args[0], ["a", "b"])
        self.assertEqual(kw["creationflags"], procs.CREATE_NEW_PROCESS_GROUP | procs.CREATE_NO_WINDOW)
        self.assertNotIn("start_new_session", kw)

    def test_windows_headless_group_kwargs(self):
        self.win()
        self.assertEqual(procs.group_kwargs(), {"creationflags": procs.CREATE_NEW_PROCESS_GROUP})


class StopTest(WinCase):
    def posix(self):
        for p in posix_patches():
            p.start()
            self.addCleanup(p.stop)

    def setUp(self):
        p = mock.patch.object(procs, "POLL", 0.01)
        p.start()
        self.addCleanup(p.stop)

    def test_posix_sigterm_then_sigkill(self):
        self.posix()
        calls = []
        with mock.patch.object(os, "kill", side_effect=lambda pid, sig: calls.append(sig)):
            ok = procs.terminate(77, 0.03, alive=lambda: True, kill_wait=0.03)
        self.assertEqual(calls, [signal.SIGTERM, signal.SIGKILL])
        self.assertFalse(ok)

    def test_posix_stops_at_sigterm_when_it_goes(self):
        self.posix()
        state = {"n": 0}

        def alive():
            state["n"] += 1
            return state["n"] < 3
        calls = []
        with mock.patch.object(os, "kill", side_effect=lambda pid, sig: calls.append(sig)):
            self.assertTrue(procs.terminate(77, 5, alive=alive))
        self.assertEqual(calls, [signal.SIGTERM])

    def test_windows_ctrl_break_then_taskkill_tree(self):
        self.win()
        brk = getattr(signal, "CTRL_BREAK_EVENT", 1)
        with mock.patch.object(signal, "CTRL_BREAK_EVENT", brk, create=True), \
                mock.patch.object(os, "kill") as kill, \
                mock.patch.object(subprocess, "run") as run:
            ok = procs.terminate(77, 0.03, alive=lambda: True, kill_wait=0.03)
        kill.assert_called_once_with(77, brk)
        self.assertEqual(run.call_args[0][0], ["taskkill", "/PID", "77", "/T", "/F"])
        self.assertFalse(ok)

    def test_windows_never_sigkill_or_sigterm(self):
        self.win()
        with mock.patch.object(signal, "CTRL_BREAK_EVENT", 1, create=True), \
                mock.patch.object(os, "kill") as kill, mock.patch.object(subprocess, "run"):
            procs.soft_stop(5, group=True)
            procs.hard_kill(5)
        self.assertEqual([c.args[1] for c in kill.call_args_list], [1])


class RunForegroundTest(WinCase):
    def test_posix_execs(self):
        for p in posix_patches():   # Windows 機でも POSIX の枝を見る
            p.start()
            self.addCleanup(p.stop)
        calls = []
        self.assertEqual(procs.run_foreground(["c", "attach", "x"], execvp=lambda f, a: calls.append((f, a))), 0)
        self.assertEqual(calls, [("c", ["c", "attach", "x"])])

    def test_windows_runs_and_returns_the_exit_code(self):
        self.win()
        execvp = mock.Mock()
        run = mock.Mock(return_value=mock.Mock(returncode=7))
        self.assertEqual(procs.run_foreground(["c", "attach", "x"], execvp=execvp, run=run), 7)
        run.assert_called_once_with(["c", "attach", "x"])
        execvp.assert_not_called()


class LivePidWindowsTest(WinCase):
    def test_pid_plus_start_time(self):
        self.win(FakeKernel32({100: None}, created={100: 555}))
        rec = {"pid": 100, "sessionId": "s"}
        self.assertEqual(headless.live_pid({**rec, "pidStart": 555}), 100)
        self.assertIsNone(headless.live_pid({**rec, "pidStart": 999}))       # pid を別のプロセスが使っている
        self.assertIsNone(headless.live_pid(rec))                            # 印が無ければ生きた -p とみなさない (T-050)
        self.assertIsNone(headless.live_pid({"pid": 300, "sessionId": "s", "pidStart": 1}))

    def test_no_pid_start_means_no_ctrl_break_to_an_orphan(self):
        """pidStart の無い roster の pid には CTRL_BREAK を送らない (コンソールごと落とす事故, T-050)。"""
        self.win(FakeKernel32({100: None}, created={100: 555}))
        with tempfile.TemporaryDirectory() as d:
            ship = Path(d)
            rec = {"state": roster.ON_SHIFT, "pid": 100, "sessionId": "s"}
            with mock.patch.object(headless.roster, "seat", return_value=rec), \
                    mock.patch.object(headless, "running", return_value=False), \
                    mock.patch.object(headless.procs, "terminate") as term, \
                    mock.patch.object(headless.procs, "soft_stop") as soft:
                self.assertFalse(headless.stop_orphan(ship, "x", "r"))
        term.assert_not_called()
        soft.assert_not_called()

    def test_terminate_sends_no_ctrl_break_to_an_unverified_pid(self):
        """down --force (headless.terminate) も pidStart で確かめた pid にしか CTRL_BREAK を送らない (T-050 差し戻し)。"""
        self.win(FakeKernel32({100: None}, created={100: 555}))
        for extra, sent in (({}, False), ({"pidStart": 999}, False), ({"pidStart": 555}, True)):
            rec = {"state": roster.ON_SHIFT, "pid": 100, "sessionId": "s", **extra}
            with tempfile.TemporaryDirectory() as d, \
                    mock.patch.object(headless.roster, "seat", return_value=rec), \
                    mock.patch.object(headless.roster, "update"), \
                    mock.patch.object(headless, "wait_idle", return_value=True), \
                    mock.patch.object(headless.procs, "soft_stop") as soft:
                headless.terminate(Path(d), "x", "r")
            self.assertEqual(soft.called, sent, extra)

    def test_windows_does_not_run_ps(self):
        self.win(FakeKernel32({100: None}))
        with mock.patch.object(headless.subprocess, "run") as run:
            headless.live_pid({"pid": 100, "sessionId": "s"})
        run.assert_not_called()


class AdmiralStopNoteTest(unittest.TestCase):
    def test_admiral_stop_note_uses_the_full_invocation(self):
        self.assertIn(f"{admiral.yamato_invocation()} seat-stop", admiral.ADMIRAL_STOP_NOTE)


class NoStraySignalsTest(unittest.TestCase):
    def test_os_differences_live_only_in_procs(self):
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "src" / "yamato"
        pat = re.compile(r"os\.kill\(|signal\.SIGKILL|\"nohup\"|start_new_session")
        for f in root.rglob("*.py"):
            if f.name == "procs.py":
                continue
            with self.subTest(file=f.name):
                self.assertIsNone(pat.search(f.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
