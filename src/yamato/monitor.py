"""Looking where the work flows (design-p1 §5.2, §5.5, §5.6): no resident watcher.

- ``check_send``: the same sender -> recipient too often, or the same text twice
  in a row (§5.5). Recorded in events.jsonl and returned as a warning for the
  sender; the send itself always goes through
- ``check_captain_gap``: on ``send`` / ``seat-stop``, a captain stopped for longer
  than ``watch.captain_gap`` gets one ``captain_gap`` event per stop (§5.2)
- ``orphans``: ``active`` items whose assignee's last shift ended without a
  handoff, or which has been stopped for ``watch.orphan_after`` (§5.6)

The thresholds are ``watch:`` in team.yaml, and each can be turned off.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from . import events, roster
from .team import watch_conf
from .util import fmt_span


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def check_send(shipdir: Path, team: dict, sender: str, to: str, entry: dict, text_digest: str,
               now: float | None = None) -> list[str]:
    """Run right after the ``send`` event of ``entry`` was written. Returns warnings."""
    spin = watch_conf(team).get("spin")
    if not spin:
        return []
    now = time.time() if now is None else now
    sends = [e for e in events.read(shipdir, kinds=events.SEND, seat=to) if e.get("by") == sender]
    warnings = []
    if spin.get("max_sends") and spin.get("window"):
        recent = [e for e in sends if e["ts"] >= now - spin["window"]]
        if len(recent) > spin["max_sends"]:
            events.emit(shipdir, events.SPIN_SUSPECTED, seat=to, by=sender,
                        summary=f"{sender} → {to} が {fmt_span(spin['window'])} に {len(recent)} 通",
                        data={"count": len(recent), "window": spin["window"], "max_sends": spin["max_sends"],
                              "n": entry["n"]})
            warnings.append(f"送りすぎ: {to} へ {fmt_span(spin['window'])} に {len(recent)} 通 "
                            f"(目安 {spin['max_sends']} 通)。board を見直し、必要なら decision を開くこと "
                            "(記録と配送はした)")
    if spin.get("same_text") and len(sends) >= 2:
        prev = (sends[-2].get("data") or {}).get("digest")
        if prev and prev == text_digest:
            events.emit(shipdir, events.DUPLICATE_SUSPECTED, seat=to, by=sender,
                        summary=f"{sender} → {to} に同じ本文を続けて送った",
                        data={"n": entry["n"], "prevN": (sends[-2].get("data") or {}).get("n")})
            warnings.append(f"重複: {to} に直前と同じ本文を送った。念押しなら問題ない "
                            "(記録と配送はした。SendMessage は短時間の同一内容を捨てることがある)")
    return warnings


def check_captain_gap(shipdir: Path, team: dict, now: float | None = None) -> dict | None:
    """One ``captain_gap`` event per stop of the captain. Returns it when written."""
    limit = watch_conf(team).get("captain_gap")
    if not limit:
        return None
    now = time.time() if now is None else now
    hub = team["hub"]
    rec = roster.seat(shipdir, hub)
    ended = rec.get("endedAt")
    if rec.get("state") != roster.OFF or not ended or now - ended < limit:
        return None
    for e in events.read(shipdir, kinds=events.CAPTAIN_GAP, since=ended):
        if (e.get("data") or {}).get("endedAt") == ended:
            return None
    return events.emit(shipdir, events.CAPTAIN_GAP, seat=hub,
                       summary=f"captain {hub} が止まってから {fmt_span(now - ended)} 起きていない",
                       data={"endedAt": ended, "shiftNo": rec.get("shiftNo"), "limit": limit})


def orphans(shipdir: Path, team: dict, now: float | None = None) -> list[tuple[dict, str]]:
    """``active`` items left by a seat that fell over (design-p1 §5.6): (item, why)."""
    from . import board as board_mod

    limit = watch_conf(team).get("orphan_after")
    now = time.time() if now is None else now
    out = []
    for meta in board_mod.Board(shipdir, team).items():
        seat = meta.get("assignee")
        if meta.get("state") != "active" or seat not in team["seats"] or seat == team["hub"]:
            continue
        rec = roster.seat(shipdir, seat)
        if rec.get("state") != roster.OFF:
            continue
        if rec.get("handoffWritten") is False or rec.get("note") == roster.NO_HANDOFF_NOTE:
            out.append((meta, f"{seat} の最後のシフト #{rec.get('shiftNo')} が引き継ぎなしで終了"))
        elif limit and rec.get("endedAt") and now - rec["endedAt"] >= limit:
            out.append((meta, f"{seat} が止まってから {fmt_span(now - rec['endedAt'])}"))
    return out
