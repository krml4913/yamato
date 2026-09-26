"""When a seat gets a fresh shift instead of carrying on (design-p1 §5.3, §5.4).

Two places ask, with the same ``roles.<role>.rotate`` settings:

- ``send`` to a stopped persistent seat (``resume_reasons``): resume, or start a
  new shift from the records
- the Stop hook of a live persistent seat (``live_reasons``): nudge it once to
  ``seat-stop --rotate`` at the next break

Both only read; the answer is a nudge or the choice between resume and a new
shift. Nothing here stops a seat (only the time limit does, §0 B4). Imported by
the per-turn hooks, so standard library and small modules only.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import roster
from .team import context_window, rotate_conf

TAIL_CHUNK = 256 * 1024
TAIL_MAX = 8 * 1024 * 1024   # a transcript is read from the end, at most this much

MESSAGE = (
    "[yamato] この席は入れ替えの時期です ({reasons})。今の仕事の区切りで、引き継ぎ (handoff.md) を書き、"
    "`{yamato} seat-stop {ship} {seat} --rotate` を実行してシフトを終えてください。"
    "次のシフトは次に誰かが send したときに記録から起きます。急ぎの途中なら区切りまで続けてよい。"
)


def last_usage(path: Path | str | None) -> dict | None:
    """The last assistant ``usage`` in a transcript: ``{"tokens", "model"}``.

    tokens = input + cache_creation + cache_read (verify-p1-d V9). May lag the
    live session by one API call; fine for a threshold, not for exact counts.
    """
    if not path:
        return None
    try:
        f = open(path, "rb")
    except OSError:
        return None
    with f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        pos, buf, head = end, b"", b""
        while pos > 0 and end - pos < TAIL_MAX:
            step = min(TAIL_CHUNK, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            lines = buf.split(b"\n")
            # the first piece may be a partial line unless we reached the start
            head, lines = (lines[0], lines[1:]) if pos > 0 else (b"", lines)
            for raw in reversed(lines):
                hit = _usage_of(raw)
                if hit:
                    return hit
            buf = head
    return None


def _usage_of(raw: bytes) -> dict | None:
    if b'"assistant"' not in raw or b'"usage"' not in raw:
        return None
    try:
        e = json.loads(raw)
    except ValueError:
        return None
    msg = e.get("message") if isinstance(e, dict) and e.get("type") == "assistant" else None
    usage = msg.get("usage") if isinstance(msg, dict) else None
    if not isinstance(usage, dict):
        return None
    tokens = sum(int(usage.get(k) or 0) for k in
                 ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return {"tokens": tokens, "model": msg.get("model")}


def context_threshold(team: dict, seat: str, model: str | None) -> int | None:
    conf = rotate_conf(team, seat).get("context")
    if not conf:
        return None
    if conf.get("tokens"):
        return int(conf["tokens"])
    window = context_window(team, model) or context_window(team, team["seats"][seat].get("model"))
    return int(window * conf["ratio"]) if window else None


def _context_reason(team: dict, seat: str, usage: dict | None) -> str | None:
    if not usage:
        return None
    limit = context_threshold(team, seat, usage.get("model"))
    if limit and usage["tokens"] >= limit:
        return f"コンテキスト {usage['tokens'] // 1000}k ≥ {limit // 1000}k"
    return None


def _hours(secs: int) -> str:
    return f"{secs / 3600:.1f}".rstrip("0").rstrip(".") + " 時間"


def live_reasons(team: dict, seat: str, rec: dict, transcript_path, now: float | None = None) -> list[str]:
    """Stop hook (§5.4): context, a compaction in this shift, the shift's length."""
    now = time.time() if now is None else now
    conf = rotate_conf(team, seat)
    out = []
    if conf.get("context"):
        r = _context_reason(team, seat, last_usage(transcript_path))
        if r:
            out.append(r)
    if conf.get("compaction") and rec.get("compactedShift") is not None \
            and rec.get("compactedShift") == rec.get("shiftNo"):
        out.append("compaction が起きた")
    if conf.get("hours") and rec.get("shiftStartedAt") and now - rec["shiftStartedAt"] >= conf["hours"]:
        out.append(f"シフトが {_hours(conf['hours'])}を超えた")
    return out


def resume_reasons(team: dict, seat: str, rec: dict, now: float | None = None) -> list[str]:
    """``send`` to a stopped persistent seat (§5.3): any reason means a new shift, none means resume."""
    from . import claude

    now = time.time() if now is None else now
    conf = rotate_conf(team, seat)
    out = []
    if rec.get("rotateRequested"):
        out.append("入れ替えの印 (seat-stop --rotate)")
    if conf.get("context") and rec.get("sessionId"):
        mains = [p for p in claude.transcript_paths(rec["sessionId"]) if p.parent.name != "subagents"]
        r = _context_reason(team, seat, last_usage(mains[0]) if mains else None)
        if r:
            out.append(r)
    stopped = rec.get("endedAt") or rec.get("lastActive")
    if conf.get("idle") and stopped and now - stopped >= conf["idle"]:
        out.append(f"止まってから {_hours(int(now - stopped))}")
    started = rec.get("shiftStartedAt")
    if conf.get("new_day") and started and time.strftime("%Y-%m-%d", time.localtime(started)) \
            != time.strftime("%Y-%m-%d", time.localtime(now)):
        out.append("日付が変わった")
    return out


def mark_compacted(shipdir: Path, seat: str) -> None:
    """PreCompact hook: remember that this shift's context was summarised."""
    rec = roster.seat(shipdir, seat)
    if rec.get("shiftNo") is not None:
        roster.update(shipdir, seat, compactedShift=rec["shiftNo"])


def take_notice(shipdir: Path, seat: str, shift_no) -> bool:
    """Once per shift: the Stop hook does not push back every turn (§5.4)."""
    from .util import ship_lock

    with ship_lock(shipdir):
        rec = roster.seat(shipdir, seat)
        if rec.get("rotateNoticeShift") == shift_no:
            return False
        roster.update(shipdir, seat, rotateNoticeShift=shift_no)
        return True
