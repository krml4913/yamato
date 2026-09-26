"""events.jsonl: the ship's append-only log of what happened (design-p1 §0.1).

One JSON object per line: ``ts`` / ``kind`` / ``seat`` / ``item`` / ``by`` /
``summary`` / ``data`` (docs/events.md). yamato's commands and hooks write it
through the ship lock (design §0 I1); the daily report (design-p1 §2) and the
monitoring (§5) read it with ``read``.

A record, not a gate (mechanism-not-policy): any kind may be written by
anyone, and nothing here checks who. A failed append is reported on stderr
and never fails the command or the hook that was recording it.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .util import ship_lock

# the kinds yamato itself writes (docs/events.md); ``emit`` accepts any other too
BOARD_ADD = "board_add"
BOARD_SET = "board_set"
BOARD_ARCHIVE = "board_archive"
SEND = "send"
SHIFT_START = "shift_start"
SHIFT_END = "shift_end"
DECISION_OPEN = "decision_open"     # P1-2 (design-p1 §1)
DECISION_CLOSE = "decision_close"   # P1-2
FORCE_STOP = "force_stop"
PERMISSION_DENIED = "permission_denied"

SUMMARY_CHARS = 200


def path(shipdir: Path) -> Path:
    return Path(shipdir) / "events.jsonl"


def emit(shipdir: Path, kind: str, *, seat: str | None = None, item: str | None = None,
         by: str | None = None, summary: str = "", data: dict | None = None,
         now: float | None = None) -> dict | None:
    """Append one event. Returns the line written, or None if the append failed."""
    summary = " ".join(str(summary).split())
    if len(summary) > SUMMARY_CHARS:
        summary = summary[:SUMMARY_CHARS] + "…"
    line = {"ts": now or time.time(), "kind": kind, "seat": seat, "item": item, "by": by,
            "summary": summary}
    if data:
        line["data"] = data
    try:
        with ship_lock(shipdir):
            with open(path(shipdir), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"yamato: events.jsonl に書けませんでした ({kind}): {e}", file=sys.stderr)
        return None
    return line


def read(shipdir: Path, *, since: float | None = None, until: float | None = None,
         kinds=None, seat: str | None = None, item: str | None = None) -> list[dict]:
    """Events in file order, filtered by ``since <= ts < until``, kind(s), seat and item."""
    if isinstance(kinds, str):
        kinds = (kinds,)
    kinds = set(kinds) if kinds else None
    try:
        lines = path(shipdir).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except ValueError:
            continue  # a torn line never blocks the rest
        if not isinstance(e, dict):
            continue
        ts = e.get("ts") or 0
        if since is not None and ts < since:
            continue
        if until is not None and ts >= until:
            continue
        if kinds is not None and e.get("kind") not in kinds:
            continue
        if seat is not None and e.get("seat") != seat:
            continue
        if item is not None and e.get("item") != item:
            continue
        out.append(e)
    return out

