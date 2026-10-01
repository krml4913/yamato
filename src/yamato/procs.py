"""プロセスの OS 差分 (Windows W3, T-042)。

生死の判定・切り離しての起動・停止・前面での attach を 1 か所に閉じ込める。ほかのモジュールは
``os.kill(pid, 0)`` / ``signal.SIGKILL`` / ``nohup`` を直接使わず、ここを呼ぶ。

Windows の事実 (work/w0-results.txt, Issue #68):
- ``os.kill(pid, 0)`` は死んだプロセスにも成功し、同じコンソールグループには Ctrl+C を送って殺す
  → ``OpenProcess`` + ``GetExitCodeProcess`` で見る (生きている = STILL_ACTIVE 259)
- 切り離しは flag なしでも ``claude stop`` を生き延びる (hook の Job は SILENT_BREAKAWAY_OK)。
  ``CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`` で起こす (CTRL_BREAK を送れるようにする)
- ``TerminateProcess`` だと ``claude -p`` の Bash の子が孤児で残る → ``CTRL_BREAK_EVENT``、
  だめなら ``taskkill /T /F`` で木ごと

Windows の分岐は ``is_windows`` と ``_kernel32`` を差し替えたテストで確かめる。
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Callable, Sequence

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5

POLL = 0.2   # terminate が生死を見る間隔 (モジュール定数。テストで縮める)


def is_windows() -> bool:
    return os.name == "nt"


def _kernel32():
    import ctypes
    return ctypes.WinDLL("kernel32", use_last_error=True)


# --- 生死 -----------------------------------------------------------------------

def _open(k32, pid: int):
    return k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if is_windows():
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes
    k32 = _kernel32()
    handle = _open(k32, pid)
    if not handle:
        # 消えた pid は WinError 87。権限が無いだけ (ACCESS_DENIED) なら居る
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def start_time(pid) -> int | None:
    """Windows: プロセスの起動時刻 (FILETIME の 64bit 値)。pid の再利用を見分ける印。
    POSIX は ``ps`` で見るので None。"""
    if not is_windows():
        return None
    import ctypes
    from ctypes import wintypes
    try:
        k32 = _kernel32()
        handle = _open(k32, int(pid))
        if not handle:
            return None
        try:
            c, e, k, u = (wintypes.FILETIME() for _ in range(4))
            if not k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e),
                                       ctypes.byref(k), ctypes.byref(u)):
                return None
            return (c.dwHighDateTime << 32) | c.dwLowDateTime
        finally:
            k32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        return None


# --- 起動 -----------------------------------------------------------------------

def group_kwargs() -> dict:
    """``claude -p`` を CTRL_BREAK で止められるようにする Popen の引数 (Windows のみ)。"""
    return {"creationflags": CREATE_NEW_PROCESS_GROUP} if is_windows() else {}


def spawn_detached(args: Sequence[str], cwd: str | None = None, env: dict | None = None,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) -> subprocess.Popen:
    """呼び出し元 (hook・席の Bash・端末) が終わっても生き残る子を起こす。"""
    kw: dict = dict(stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=env, cwd=cwd)
    if is_windows():
        return subprocess.Popen(list(args), creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW, **kw)
    return subprocess.Popen(["nohup", *args], start_new_session=True, **kw)


# --- 停止 -----------------------------------------------------------------------

def soft_stop(pid, group: bool = True) -> None:
    """穏やかに止める: POSIX は SIGTERM、Windows は CTRL_BREAK_EVENT (相手が自分のグループの先頭のとき)。
    ``group=False`` (グループの先頭として起こしたと確かめられない pid) の Windows は、CTRL_BREAK が
    同じコンソール全体に届くので送らず、hard_kill (taskkill /T /F) に回す。"""
    if is_windows() and not group:
        hard_kill(pid)
        return
    sig = signal.CTRL_BREAK_EVENT if is_windows() else signal.SIGTERM   # type: ignore[attr-defined]
    try:
        os.kill(int(pid), sig)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        if not is_windows():
            raise


def hard_kill(pid) -> None:
    """強制: POSIX は SIGKILL、Windows は ``taskkill /T /F`` (木ごと)。"""
    if is_windows():
        try:
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True,
                           timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    try:
        os.kill(int(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def terminate(pid, grace: float, alive: Callable[[], bool] | None = None, kill_wait: float = 5,
              group: bool = True) -> bool:
    """soft_stop → ``grace`` 秒待つ → hard_kill → ``kill_wait`` 秒待つ。消えたら True。
    ``alive``: 引数なしの生死の判定 (既定は pid_alive(pid)。pid の再利用を見分けたいときに渡す)。
    ``group``: soft_stop に渡す (group_kwargs で起こした pid か)。"""
    alive = alive or (lambda: pid_alive(pid))
    for stop, wait in ((soft_stop, grace), (hard_kill, kill_wait)):
        if not alive():
            return True
        stop(pid, group) if stop is soft_stop else stop(pid)
        end = time.time() + wait
        while time.time() < end and alive():
            time.sleep(POLL)
    return not alive()


# --- 前面 -----------------------------------------------------------------------

def run_foreground(argv: Sequence[str], *, execvp=os.execvp, run=subprocess.run) -> int:
    """端末を渡して前面で走らせる。POSIX は exec (戻らない。差し替えた execvp のときだけ 0 で戻る)、
    Windows は待って終了コードを返す (execvp は別プロセスを作って即戻るので使えない)。"""
    if is_windows():
        return run(list(argv)).returncode
    execvp(argv[0], list(argv))
    return 0
