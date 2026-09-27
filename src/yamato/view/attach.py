"""seat-attach: keep a pane attached to a seat's current shift (design §10).

Rules from docs/_archive/spike-zellij-attach.md:
- attach only to a live session (``pid != null``). Attaching to a stopped one
  wakes it up again (Q4), which would resurrect an old shift.
- ``--name`` is not unique (Q5), so "the current shift" comes from roster.json.
- ``claude attach`` runs in the foreground on the pane's tty. Backgrounding it
  dies on macOS with ``EINVAL ... kqueue``. Here it is a plain child that
  shares our process group and tty; the watching happens in this process.
- ``claude attach`` takes the short id (``claude agents``' ``id``); the full
  sessionId gives ``No job matching`` (2.1.283, checked in this task).
- A stopped/removed session makes ``claude attach`` exit 0 by itself (Q4), so
  the loop only has to notice a *newer* shift and move over to it.
"""
from __future__ import annotations

import subprocess
import sys
import time
from typing import Callable

from .. import claude, roster
from ..util import YamatoError

# Returns what to pass to ``claude attach`` for the seat's current shift
# (already checked to be alive), or None. A change in the value means a new shift.
Resolver = Callable[[], str | None]

DEFAULT_POLL = 3.0


# --- resolvers ---------------------------------------------------------------

def roster_resolver(shipdir, seat: str, *,
                    agents: Callable[[], list[dict]] = claude.agents,
                    alive: Callable[[dict | None], bool] = claude.is_alive) -> Resolver:
    """Default: roster.json's sessionId for the seat, if that session is alive.

    Matched on the full sessionId; returns the session's short ``id`` for attach.
    roster.json is read without the ship lock: P0 writes it atomically.
    """
    def resolve() -> str | None:
        sid = roster.seat(shipdir, seat).get("sessionId")
        if not sid:
            return None
        rec = claude.by_session(agents()).get(sid)
        if not alive(rec):
            return None
        return rec.get("id") or sid[:8]
    return resolve


# --- the attach loop ---------------------------------------------------------

def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _reset_tty(out) -> None:
    # A terminated `claude attach` may leave the alternate screen / hidden cursor behind.
    out.write("\x1b[?1049l\x1b[?25h\x1b[0m\r\n")
    out.flush()


def watch(proc: subprocess.Popen, sid: str, resolve: Resolver, *, poll: float,
          on_error: Callable[[Exception], None] = lambda e: None) -> str | None:
    """Wait for the attach to end. If a different live shift shows up first,
    stop the attach and return that sessionId; otherwise return None."""
    while True:
        try:
            proc.wait(timeout=poll)
            return None
        except subprocess.TimeoutExpired:
            pass
        try:
            new = resolve()
        except YamatoError as e:
            on_error(e)
            continue
        # None (roster empty / new shift not up yet) keeps the current attach:
        # it ends by itself when its session stops.
        if new and new != sid:
            _stop(proc)
            return new


def run(resolve: Resolver, label: str, *, poll: float = DEFAULT_POLL, out=None,
        spawn: Callable[[list[str]], subprocess.Popen] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
        rounds: int | None = None) -> int:
    """Attach to whatever ``resolve`` names, follow it across shifts, forever.

    ``rounds`` limits the number of loop iterations (tests only).
    """
    out = out or sys.stdout
    status_line = False

    def status(msg: str) -> None:
        nonlocal status_line
        out.write(f"\r\x1b[K[{label}] {msg}")
        out.flush()
        status_line = True

    def say(msg: str) -> None:
        nonlocal status_line
        out.write(("\r\n" if status_line else "") + f"[{label}] {msg}\r\n")
        out.flush()
        status_line = False

    proc = None
    try:
        while rounds is None or rounds > 0:
            if rounds is not None:
                rounds -= 1
            try:
                sid = resolve()
            except YamatoError as e:
                status(f"席の状態を確認できません: {e} ({time.strftime('%H:%M:%S')})")
                sleep(poll)
                continue
            if not sid:
                status(f"待機中 (今のシフトなし, {time.strftime('%H:%M:%S')} 確認)")
                sleep(poll)
                continue

            say(f"attach {sid}")
            proc = spawn([claude.claude_bin(), "attach", sid])
            replaced = watch(proc, sid, resolve, poll=poll)
            code = proc.returncode
            proc = None
            if replaced:
                _reset_tty(out)
                say(f"新しいシフトに付け直します ({sid} -> {replaced})")
                continue
            say(f"attach が終わりました (exit {code})")
            # a failing attach (e.g. the session vanished in between) must not spin
            sleep(1 if code == 0 else poll)
    except KeyboardInterrupt:
        if proc is not None:
            _stop(proc)
        return 130
    return 0
