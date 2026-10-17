"""Find `view attach` / `claude attach` processes that outlive their pane (T-089).

A pane's ``yamato view attach`` runs ``claude attach`` as a child. When zellij closes the pane
(a rebuilt tab, ``delete-session``, a killed server) the pane process gets SIGHUP/SIGTERM; if
it dies without stopping the child, the ``claude attach`` is orphaned (ppid 1) and stays
attached to the seat's session. A new pane's attach then fights it. Nothing here kills
anything unless the caller asks (``view stale --kill``).
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    argv: tuple[str, ...]

    @property
    def text(self) -> str:
        return " ".join(self.argv)


def ps_list() -> list[Proc]:
    """All processes via ``ps`` (POSIX). Where ``ps`` is missing or fails: empty."""
    try:
        cp = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if cp.returncode != 0:
        return []
    out = []
    for line in (cp.stdout or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not (parts[0].isdigit() and parts[1].isdigit()):
            continue
        try:
            argv = tuple(shlex.split(parts[2]))
        except ValueError:
            argv = tuple(parts[2].split())
        out.append(Proc(int(parts[0]), int(parts[1]), argv))
    return out


def _is_view_attach(p: Proc) -> bool:
    a = p.argv
    return any(a[i:i + 2] == ("view", "attach") for i in range(len(a) - 1))


def _is_claude_attach(p: Proc) -> bool:
    """``<...claude...> attach <id>``"""
    a = p.argv
    return len(a) >= 3 and a[-2] == "attach" and any(os.path.basename(x).startswith("claude") for x in a[:-2])


def _ancestors(procs: list[Proc], pid: int) -> set[int]:
    by = {p.pid: p for p in procs}
    seen = {pid}
    while pid in by and by[pid].ppid not in seen and by[pid].ppid > 1:
        pid = by[pid].ppid
        seen.add(pid)
    return seen


def other_view_attachers(ship: str, seat: str, *, me: int | None = None,
                         procs: Callable[[], list[Proc]] = ps_list) -> list[Proc]:
    """Other ``yamato view attach <ship> <seat>`` processes (not me, not my ancestors)."""
    plist = procs()
    skip = _ancestors(plist, me if me is not None else os.getpid())
    return [p for p in plist if p.pid not in skip and _is_view_attach(p)
            and len(p.argv) >= 2 and p.argv[-2:] == (ship, seat)]


def orphans(*, procs: Callable[[], list[Proc]] = ps_list) -> list[Proc]:
    """`view attach` / `claude attach` processes whose parent is gone (ppid 1)."""
    return [p for p in procs() if p.ppid == 1 and (_is_view_attach(p) or _is_claude_attach(p))]


def terminate(pids: list[int], *, kill: Callable[[int, int], None] = os.kill) -> list[int]:
    """SIGTERM each pid; returns those signalled."""
    done = []
    for pid in pids:
        try:
            kill(pid, signal.SIGTERM)
            done.append(pid)
        except OSError:
            pass
    return done
