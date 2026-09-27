"""Shared helpers for the Windows verification kit (docs/verify/verify-win-plan.md).

Standard library only. Never imports yamato (src/). Windows-only calls are made
at run time behind ``IS_WIN``, so the module also imports on macOS / Linux.
"""
from __future__ import annotations

import ctypes
import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path

IS_WIN = os.name == "nt"

# creationflags (subprocess has them only on Windows)
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

KIT = Path(__file__).resolve().parent

# Injected tokens: the ASCII key goes through argv, the Japanese part only through the
# hook's stdout (so a garbled token means the hook output, not the command line)
TOKENS = {"SHELL": "桜", "EXEC8": "富士", "EXECDEF": "紅葉", "EXECASC": "雪", "PROF": "月", "PROFX": "星"}


def token(key: str) -> str:
    return f"VWTOK-{key}-{TOKENS.get(key, '?')}"


def vw_dir() -> Path:
    """The throwaway folder. The copied kit lives in <VW>/kit, so hooks find it from
    their own location (bg seats are started by the daemon and may not see VW_DIR)."""
    if KIT.name == "kit" and (KIT.parent / ".vw").exists():
        return KIT.parent
    env = os.environ.get("VW_DIR")
    return Path(env) if env else Path.home() / "yamato-verify-win"


def fwd(p) -> str:
    """C:\\Users\\x -> C:/Users/x (passes through Git Bash, native exes and Python alike)."""
    return str(p).replace("\\", "/")


def msys(p) -> str:
    """C:\\Users\\x -> /c/Users/x (the form Git Bash and permission rules use)."""
    s = fwd(p)
    if len(s) >= 2 and s[1] == ":":
        return "/" + s[0].lower() + s[2:]
    return s


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def utf8_stdio() -> None:
    """Show Japanese correctly in mintty. Call after recording the original encodings."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def run(cmd, timeout=60, **kw) -> dict:
    """Run a command; never raise. Output is decoded as UTF-8 with replacement
    (the raw bytes' validity is reported separately)."""
    try:
        cp = subprocess.run(cmd, capture_output=True, timeout=timeout,
                            stdin=subprocess.DEVNULL, **kw)
    except FileNotFoundError as e:
        return {"cmd": cmd, "error": f"FileNotFoundError: {e}"}
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "error": f"timeout {timeout}s"}
    except OSError as e:
        return {"cmd": cmd, "error": f"OSError: {e}"}
    return {"cmd": cmd, "rc": cp.returncode,
            "out": decode(cp.stdout)[0], "err": decode(cp.stderr)[0],
            "out_utf8": decode(cp.stdout)[1]}


def decode(b: bytes) -> tuple[str, bool]:
    try:
        return b.decode("utf-8"), True
    except UnicodeDecodeError:
        return b.decode("utf-8", errors="replace"), False


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- Windows API

if IS_WIN:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetExitCodeProcess.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    _k32.IsProcessInJob.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _EXT(ctypes.Structure):
        _fields_ = [("Basic", _BASIC),
                    ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
_JOB_FLAGS = {0x2000: "KILL_ON_JOB_CLOSE", 0x800: "BREAKAWAY_OK",
              0x1000: "SILENT_BREAKAWAY_OK", 0x400: "DIE_ON_UNHANDLED_EXCEPTION",
              0x8: "ACTIVE_PROCESS"}


def pid_alive(pid: int) -> dict:
    """Windows: OpenProcess + GetExitCodeProcess. POSIX: os.kill(pid, 0).
    Returns {"alive": True/False/None, ...details}."""
    if not IS_WIN:
        try:
            os.kill(pid, 0)
            return {"alive": True, "how": "os.kill(pid,0)"}
        except ProcessLookupError:
            return {"alive": False, "how": "os.kill(pid,0)"}
        except PermissionError:
            return {"alive": True, "how": "os.kill(pid,0) EPERM"}
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        err = ctypes.get_last_error()
        # 87 = ERROR_INVALID_PARAMETER: no such pid. 5 = access denied: exists.
        return {"alive": True if err == 5 else False if err == 87 else None,
                "how": "OpenProcess", "winerror": err}
    try:
        code = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return {"alive": None, "how": "GetExitCodeProcess", "winerror": ctypes.get_last_error()}
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        image = buf.value if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else None
        return {"alive": code.value == STILL_ACTIVE, "how": "OpenProcess+GetExitCodeProcess",
                "exit_code": code.value, "image": image}
    finally:
        _k32.CloseHandle(h)


def job_info() -> dict:
    """Is this process in a Job Object, and with which limits (KILL_ON_JOB_CLOSE /
    BREAKAWAY_OK decide whether a detached child survives the seat's stop)."""
    if not IS_WIN:
        try:
            return {"platform": "posix", "pgid": os.getpgid(0), "sid": os.getsid(0)}
        except OSError as e:
            return {"platform": "posix", "error": str(e)}
    res = wintypes.BOOL()
    if not _k32.IsProcessInJob(_k32.GetCurrentProcess(), None, ctypes.byref(res)):
        return {"in_job": None, "winerror": ctypes.get_last_error()}
    out = {"in_job": bool(res.value)}
    if res.value:
        info = _EXT()
        if _k32.QueryInformationJobObject(None, 9, ctypes.byref(info), ctypes.sizeof(info), None):
            flags = info.Basic.LimitFlags
            out["limit_flags"] = hex(flags)
            out["limits"] = [n for bit, n in _JOB_FLAGS.items() if flags & bit]
        else:
            out["query_winerror"] = ctypes.get_last_error()
    return out
