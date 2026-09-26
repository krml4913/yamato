"""roster.json: which session is each seat's current shift, how shifts ended, and
when each seat last moved (``lastActive``, design-p1 §5.1).

The source of truth for "the seat's current session" (``--name`` is not
unique, spike-zellij-attach). Always stores the full sessionId: a short id
passed to ``--resume`` starts a copy (verify-p0-a Q2).
"""
from __future__ import annotations

import time
from pathlib import Path

from . import events
from .util import read_json, ship_lock, write_json

MAX_SHIFTS = 200

ON_SHIFT = "on_shift"   # launched and not known to have ended
STOPPING = "stopping"   # seat-stop accepted, delayed stop pending
OFF = "off"             # ended

NO_HANDOFF_NOTE = "引き継ぎなしで終了"


def _path(shipdir: Path) -> Path:
    return Path(shipdir) / "roster.json"


def load(shipdir: Path) -> dict:
    data = read_json(_path(shipdir), None) or {}
    data.setdefault("seats", {})
    data.setdefault("shifts", [])
    return data


def save(shipdir: Path, data: dict) -> None:
    data["shifts"] = data["shifts"][-MAX_SHIFTS:]
    write_json(_path(shipdir), data)


def seat(shipdir: Path, name: str) -> dict:
    return load(shipdir)["seats"].get(name, {})


def seat_of_session(shipdir: Path, session_id: str | None) -> str | None:
    """The seat whose shift is ``session_id`` (who called a command; for the record only).

    The current shift first, then the seat's earlier shifts (a session that
    outlived its shift is still that seat's)."""
    if not session_id:
        return None
    data = load(shipdir)
    for name, rec in data["seats"].items():
        if rec.get("sessionId") == session_id:
            return name
    for sh in reversed(data["shifts"]):
        if sh.get("sessionId") == session_id:
            return sh["seat"]
    return None


def start_shift(shipdir: Path, name: str, *, session_id: str, short_id: str, session_name: str,
                how: str, now: float | None = None, cwd: str | None = None,
                rotated: list[str] | None = None) -> dict:
    """``cwd``: the shift runs somewhere other than the workspace (``send --cwd``).
    ``rotated``: why a persistent seat got a new shift instead of a resume (design-p1 §5.3)."""
    now = time.time() if now is None else now
    with ship_lock(shipdir):
        data = load(shipdir)
        rec = data["seats"].setdefault(name, {})
        # the next shift reads the work-log tail too when this one left no handoff (§12.1)
        prev_no_handoff = rec.get("shiftNo") is not None and (
            rec.get("handoffWritten") is False or rec.get("state") == ON_SHIFT)
        rec.update({
            "prevEndedWithoutHandoff": prev_no_handoff,
            "sessionId": session_id,
            "shortId": short_id,
            "name": session_name,
            "state": ON_SHIFT,
            "shiftNo": rec.get("shiftNo", 0) + 1,
            "shiftStartedAt": now,
            "lastActive": now,
            "how": how,
            "endedAt": None,
            "endReason": None,
            "handoffWritten": None,
            "note": None,
            "cwd": cwd,
            "rotated": rotated or None,
            "rotateRequested": None,   # the mark of seat-stop --rotate is used up (design-p1 §5.4)
        })
        shift = {"seat": name, "shiftNo": rec["shiftNo"], "sessionId": session_id,
                 "how": how, "startedAt": now, "endedAt": None, "endReason": None}
        extra = {k: v for k, v in (("cwd", cwd), ("rotated", rotated)) if v}
        shift.update(extra)
        data["shifts"].append(shift)
        save(shipdir, data)
        events.emit(shipdir, events.SHIFT_START, seat=name, now=now,
                    summary=f"シフト開始 #{rec['shiftNo']} ({how})"
                    + (f" 入れ替え: {', '.join(rotated)}" if rotated else ""),
                    data={"shiftNo": rec["shiftNo"], "how": how, "sessionId": session_id, **extra})
        return dict(rec)


def touch(shipdir: Path, name: str, now: float | None = None) -> None:
    """The seat just moved (SessionStart / UserPromptSubmit / Stop hooks, design-p1 §5.1).

    A seat roster does not know yet (never started a shift) is left alone: a
    hook must not create a half-filled seat record."""
    with ship_lock(shipdir):
        data = load(shipdir)
        rec = data["seats"].get(name)
        if rec is None:
            return
        rec["lastActive"] = time.time() if now is None else now
        save(shipdir, data)


def update(shipdir: Path, name: str, **fields) -> dict:
    """Set extra keys on a seat's record (a headless shift's pids and outcome)."""
    with ship_lock(shipdir):
        data = load(shipdir)
        rec = data["seats"].setdefault(name, {})
        rec.update(fields)
        save(shipdir, data)
        return dict(rec)


def mark_stopping(shipdir: Path, name: str, *, handoff_written: bool, now: float | None = None,
                  rotate: bool = False) -> dict:
    """``rotate``: seat-stop --rotate; the next wake starts a new shift (design-p1 §5.4)."""
    with ship_lock(shipdir):
        data = load(shipdir)
        rec = data["seats"].setdefault(name, {})
        rec.update({"state": STOPPING, "handoffWritten": handoff_written, "stopRequestedAt": time.time() if now is None else now})
        if rotate:
            rec["rotateRequested"] = True
        save(shipdir, data)
        return dict(rec)


def end_shift(shipdir: Path, name: str, *, reason: str, handoff_written: bool | None = None,
              note: str | None = None, now: float | None = None, extra: dict | None = None) -> dict:
    """``extra`` goes on both the seat and the shift (a headless shift's outcome, rate limit)."""
    now = time.time() if now is None else now
    with ship_lock(shipdir):
        data = load(shipdir)
        rec = data["seats"].setdefault(name, {})
        if handoff_written is not None:
            rec["handoffWritten"] = handoff_written
        rec.update({"state": OFF, "endedAt": now, "endReason": reason, "note": note, **(extra or {})})
        for sh in reversed(data["shifts"]):
            if sh["seat"] == name and sh.get("shiftNo") == rec.get("shiftNo"):
                sh.update({"endedAt": now, "endReason": reason, "handoffWritten": rec.get("handoffWritten"),
                           **(extra or {})})
                if note:
                    sh["note"] = note
                break
        save(shipdir, data)
        events.emit(shipdir, events.SHIFT_END, seat=name, now=now,
                    summary=f"シフト終了 #{rec.get('shiftNo')} ({reason})" + (f" {note}" if note else ""),
                    data={"shiftNo": rec.get("shiftNo"), "reason": reason,
                          "handoffWritten": rec.get("handoffWritten"), "note": note})
        return dict(rec)
