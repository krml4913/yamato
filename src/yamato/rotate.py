"""When a seat gets a fresh shift instead of carrying on (design-p1 §5.3, §5.4).

Two places ask, with the same ``roles.<role>.rotate`` settings:

- ``send`` to a stopped persistent seat (``resume_reasons``): resume, or start a
  new shift from the records
- the Stop hook of a live persistent seat (``live_reasons``): nudge it once to
  ``seat-stop --rotate`` at the next break

Both only read; the answer is a nudge or the choice between resume and a new
shift. Nothing here stops a seat (only the time limit does, §0 B4). Imported by
the per-turn hooks, so standard library and small modules only.

A third place *writes* the same mark: ``yamato rotate <ship> <seat>...`` (T-024,
design-drift D). A stopped persistent seat has no session to run ``seat-stop
--rotate`` from (a team-composition change means its next shift should start
fresh, but nobody is there to ask for that) -- this command sets
``rotateRequested`` from outside, the same mark ``resume_reasons`` already reads.
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


# --- `yamato rotate` (T-024): mark a *stopped* persistent seat from outside -----------

def persistent_seats(team: dict) -> list[str]:
    return [name for name, spec in team["seats"].items() if spec["shift"] == "persistent"]


def request(shipdir: Path, team: dict, seats: list[str], *, by: str | None = None) -> dict[str, str | None]:
    """Set ``rotateRequested`` on each of ``seats`` (design-p1 §5.4, T-024): the same mark
    ``resume_reasons`` reads, but written from outside the seat's own session -- for a
    *stopped* persistent seat, so its next ``send`` starts a fresh shift (new ``--agents``,
    say, after a team-composition change) instead of a resume.

    Returns ``{seat: None}`` for every seat marked, or ``{seat: <理由>}`` when refused: a
    live seat (its own Stop hook already nudges it to ``seat-stop --rotate``; marking the
    roster here would sit unused until it stops on its own), or a per_task/headless one
    (neither ever resumes, so the mark would never be read). An unknown seat name is a
    caller error (``YamatoError``), the same as every other command taking a seat name."""
    from . import claude, events
    from .team import seat_spec

    specs = {name: seat_spec(team, name) for name in seats}   # an unknown name raises before anything is marked
    listing = claude.agents()
    by_sid = claude.by_session(listing)
    out: dict[str, str | None] = {}
    for name in seats:
        spec = specs[name]
        if spec["shift"] != "persistent":
            out[name] = f"shift: {spec['shift']} の席には立てられない (resume がないので入れ替えの印を読まない)"
            continue
        rec = roster.seat(shipdir, name)
        if claude.is_alive(by_sid.get(rec.get("sessionId"))):
            out[name] = "生きている席には立てられない (seat-stop --rotate を促すか、止まってから実行してください)"
            continue
        roster.update(shipdir, name, rotateRequested=True)
        events.emit(shipdir, events.ROTATE_REQUESTED, seat=name, by=by,
                    summary=f"入れ替えの印を立てた ({by})" if by else "入れ替えの印を立てた",
                    data={"shiftNo": rec.get("shiftNo")})
        out[name] = None
    return out


def register(sub) -> None:
    r = sub.add_parser("rotate", help="止まっている persistent の席に入れ替えの印を立てる (次の send で新しいシフト)")
    r.add_argument("ship")
    r.add_argument("seat", nargs="*", help="対象の席 (複数可)。--all のときは省く")
    r.add_argument("--all", action="store_true", help="persistent の席すべて")
    r.add_argument("--by")


def run(args) -> int:
    import os

    from .seat import current_team
    from .util import YamatoError, resolve_ship

    shipdir = resolve_ship(args.ship)
    team = current_team(shipdir)
    if args.all == bool(args.seat):
        raise YamatoError("席名を 1 つ以上指定するか、--all を付けてください (両方はできません)")
    seats = persistent_seats(team) if args.all else list(args.seat)
    if not seats:
        print("persistent の席がありません")
        return 0
    by = args.by or roster.seat_of_session(shipdir, os.environ.get("CLAUDE_CODE_SESSION_ID")) or "owner"
    results = request(shipdir, team, seats, by=by)
    for name in seats:
        reason = results[name]
        if reason is None:
            print(f"{name}: 入れ替えの印を立てた (次に send したとき、resume せず新しいシフトになる)")
        else:
            print(f"{name}: 立てなかった ({reason})")
    return 0
