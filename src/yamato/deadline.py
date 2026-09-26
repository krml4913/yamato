"""The ship's time limit as data (design §0 B4): ``.runtime/deadline``.

Checked by the seat hooks and by ``send`` every time, so the limit holds even
if the one-shot watchdog process dies.
"""
from __future__ import annotations

import time
from pathlib import Path

from .util import fmt_span, fmt_time, read_json, ship_lock, write_json

NOT_UP = "not_up"      # no deadline file: the ship has not been brought up
RUNNING = "running"    # before the deadline
OVER = "over"          # past the deadline, inside the grace period: wrap up
FORCE = "force"        # past deadline + grace: stop whatever is still alive

WRAP_UP_MESSAGE = (
    "[yamato] 艦の稼働時間の上限を過ぎました。新しい作業は始めず、キリのいいところで止めて、"
    "引き継ぎ (handoff.md) を書き、`{yamato} seat-stop {ship} {seat}` を実行してシフトを終えてください。"
)


def path(shipdir: Path) -> Path:
    return Path(shipdir) / ".runtime" / "deadline"


def read(shipdir: Path) -> dict | None:
    return read_json(path(shipdir), None)


def write(shipdir: Path, *, limit: int, grace: int, token: str, now: float | None = None) -> dict:
    now = now or time.time()
    data = {
        "upAt": now,
        "deadline": now + limit,
        "graceUntil": now + limit + grace,
        "grace": grace,
        "token": token,
    }
    with ship_lock(shipdir):
        write_json(path(shipdir), data)
    return data


def write_raw(shipdir: Path, data: dict) -> None:
    with ship_lock(shipdir):
        write_json(path(shipdir), data)


def end_now(shipdir: Path, now: float | None = None) -> dict | None:
    """Manual ``down``: the deadline becomes now, the grace period starts."""
    now = now or time.time()
    with ship_lock(shipdir):
        data = read(shipdir)
        if not data:
            return None
        if data["deadline"] > now:
            data["deadline"] = now
            data["graceUntil"] = now + data.get("grace", 0)
        write_json(path(shipdir), data)
        return data


def extend(shipdir: Path, seconds: int, now: float | None = None) -> tuple[dict | None, bool]:
    """``yamato extend`` (design-p1 §6.2): move the deadline later. Data only; the
    watchdog and the hooks' watcher re-read the file on every poll.

    Counted from the current deadline, or from now once it has passed (after
    ``down`` or past the limit, "1h more" means an hour from now). Returns
    (the new data, whether the deadline had already passed).
    """
    now = now or time.time()
    with ship_lock(shipdir):
        data = read(shipdir)
        if not data:
            return None, False
        was_over = data["deadline"] <= now
        data["deadline"] = max(data["deadline"], now) + seconds
        data["graceUntil"] = data["deadline"] + data.get("grace", 0)
        write_json(path(shipdir), data)
        return data, was_over


def phase(data: dict | None, now: float | None = None) -> str:
    if not data:
        return NOT_UP
    now = now or time.time()
    if now < data["deadline"]:
        return RUNNING
    if now < data["graceUntil"]:
        return OVER
    return FORCE


def describe(data: dict | None, now: float | None = None) -> str:
    now = now or time.time()
    p = phase(data, now)
    if p == NOT_UP:
        return "未起動 (deadline なし)"
    if p == RUNNING:
        return f"deadline {fmt_time(data['deadline'])} (残り {fmt_span(data['deadline'] - now)})"
    if p == OVER:
        return f"終業中: deadline {fmt_time(data['deadline'])} を過ぎた (強制停止まで {fmt_span(data['graceUntil'] - now)})"
    return f"強制停止の時刻 {fmt_time(data['graceUntil'])} を過ぎた"
