"""Small child processes for the Windows verification kit.

  child.py sleep <label> <seconds>   write <VW>/detach/<label>.start, a .beat every 2 s,
                                     and <label>.done at the end (did it outlive the seat?)
  child.py lockprobe <path> <mode>   try msvcrt.locking on byte 0 (mode: nb | lk); print JSON
  child.py oskill <pid>              call os.kill(pid, 0) and print what happened as JSON
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import IS_WIN, job_info, now, vw_dir, write_json  # noqa: E402


def sleep(label: str, seconds: float) -> None:
    d = vw_dir() / "detach"
    base = {"label": label, "pid": os.getpid(), "ppid": os.getppid(), "job": job_info()}
    write_json(d / f"{label}.start", {**base, "t": now(), "epoch": time.time()})
    end = time.time() + seconds
    while time.time() < end:
        write_json(d / f"{label}.beat", {"t": now(), "epoch": time.time()})
        time.sleep(min(2.0, max(0.0, end - time.time())))
    write_json(d / f"{label}.done", {**base, "t": now(), "epoch": time.time()})


def lockprobe(path: str, mode: str) -> None:
    import msvcrt  # Windows only; the caller checks the platform
    t0 = time.time()
    out = {"mode": mode, "pid": os.getpid()}
    with open(path, "r+b") as f:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK if mode == "nb" else msvcrt.LK_LOCK, 1)
            out["locked"] = True
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError as e:
            out["locked"] = False
            out["error"] = f"{type(e).__name__}: errno={e.errno} {e}"
    out["elapsed"] = round(time.time() - t0, 2)
    print(json.dumps(out))


def oskill(pid: int) -> None:
    out = {"target": pid, "caller": os.getpid()}
    try:
        r = os.kill(pid, 0)
        out["result"] = f"returned {r!r}"
    except BaseException as e:  # KeyboardInterrupt too: a CTRL_C_EVENT may land here
        out["result"] = f"{type(e).__name__}: {e}"
        out["winerror"] = getattr(e, "winerror", None)
    time.sleep(1)  # let a console Ctrl+C, if any, arrive before we report
    out["still_here"] = True
    print(json.dumps(out))


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "sleep":
        sleep(argv[1], float(argv[2]))
    elif len(argv) >= 3 and argv[0] == "lockprobe" and IS_WIN:
        lockprobe(argv[1], argv[2])
    elif len(argv) >= 2 and argv[0] == "oskill":
        oskill(int(argv[1]))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
