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
BOARD_NOTE = "board_note"           # P1-10 (design-p1 §7.2)
SEND = "send"
SHIFT_START = "shift_start"
SHIFT_END = "shift_end"
DECISION_OPEN = "decision_open"     # P1-2 (design-p1 §1)
DECISION_CLOSE = "decision_close"   # P1-2
FORCE_STOP = "force_stop"
SHIFT_FAILED = "shift_failed"     # a headless shift that failed (design-p1 §4.2, §4.4)
LAUNCH_FAILED = "launch_failed"   # a bg seat's launch / resume that did not come up (verify-p0-c Q5)
PERMISSION_DENIED = "permission_denied"
# P1-5/7 (design-p1 §5.2-5.5, §9): records and warnings, never refusals
SPIN_SUSPECTED = "spin_suspected"           # one sender -> one recipient, too many sends in a window
DUPLICATE_SUSPECTED = "duplicate_suspected" # the same text twice in a row to the same recipient
CAPTAIN_GAP = "captain_gap"                 # the captain has been stopped for longer than watch.captain_gap
ROTATE_SUGGESTED = "rotate_suggested"       # the Stop hook nudged a live seat to seat-stop --rotate
ROTATE_REQUESTED = "rotate_requested"       # seat-stop --rotate set the mark
LAST_CALL = "last_call"                     # the captain was told the last call has passed
RESTOP_FAILED = "restop_failed"             # T-012: spawning a seat-stop's delayed stop itself failed
STOPPING_STUCK = "stopping_stuck"           # T-012: `stopping` past STOPPING_STUCK_AFTER; forced again

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
    line = {"ts": time.time() if now is None else now, "kind": kind, "seat": seat, "item": item, "by": by,
            "summary": summary}
    if data:
        line["data"] = data
    try:
        with ship_lock(shipdir):
            with open(path(shipdir), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as e:  # TypeError / ValueError: data that JSON cannot hold
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
        ts = e.get("ts")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            continue  # a line without a numeric time cannot be placed in a period
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

