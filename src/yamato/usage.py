"""Token usage per shift (design §0 I7), counted from the session transcript.

A persistent seat resumes the same transcript across shifts, so only the
assistant messages timestamped inside the shift are counted. Streaming writes
one line per content block with the same ``message.id``; the last one wins.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from . import claude
from .util import ship_lock

KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def count(paths: list[Path], since: float, until: float | None = None) -> dict:
    """``read``: how many transcripts could be read. 0 means the usage is unknown (the
    transcripts live in Claude Code's internal layout), not that nothing was used."""
    per_msg: dict = {}
    models: set = set()
    read = 0
    for p in paths:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        read += 1
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            msg = e.get("message")
            if e.get("type") != "assistant" or not isinstance(msg, dict) or not msg.get("usage"):
                continue
            t = _epoch(e.get("timestamp"))
            if t is None or t < since or (until is not None and t > until):
                continue
            per_msg[(p.name, msg.get("id") or id(e))] = msg["usage"]
            if msg.get("model"):
                models.add(msg["model"])
    totals = {k: sum(int(u.get(k) or 0) for u in per_msg.values()) for k in KEYS}
    totals["messages"] = len(per_msg)
    totals["models"] = sorted(models)
    totals["read"] = read
    return totals


def build(seat: str, *, session_id: str, shift_no: int | None,
          since: float, until: float | None = None) -> dict:
    """The ``usage.jsonl`` line, computed by parsing the transcript. Read-only (no
    lock): #10 has ``seat.finish_shift`` call this before it takes the ship lock,
    so a slow transcript read never holds up anyone else's write."""
    until = until or time.time()
    totals = count(claude.transcript_paths(session_id), since, until)
    read = totals.pop("read")
    line = {
        "ts": until, "seat": seat, "shiftNo": shift_no, "sessionId": session_id,
        "startedAt": since, "endedAt": until, **totals,
        "total_tokens": sum(totals[k] for k in KEYS),
    }
    if not read:
        line["unknown"] = True
    return line


def record(shipdir: Path, seat: str, *, session_id: str, shift_no: int | None,
           since: float, until: float | None = None) -> dict:
    """``build`` + ``append`` in one call, for callers that don't need the ship
    lock released while the transcript is parsed."""
    line = build(seat, session_id=session_id, shift_no=shift_no, since=since, until=until)
    append(shipdir, line)
    return line


def append(shipdir: Path, line: dict) -> None:
    with ship_lock(shipdir):
        with open(Path(shipdir) / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def summary(line: dict) -> str:
    if line.get("unknown"):
        return f"使用量 shift#{line.get('shiftNo')}: 分からない (transcript を読めなかった)"
    return (f"使用量 shift#{line.get('shiftNo')}: in={line['input_tokens']} out={line['output_tokens']} "
            f"cache_write={line['cache_creation_input_tokens']} cache_read={line['cache_read_input_tokens']} "
            f"(計 {line['total_tokens']}, {line['messages']} messages)")
